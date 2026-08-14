package com.agora.gateway.agent;

import com.agora.gateway.config.GatewayProperties;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.net.URI;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

@Service
public class AgentRegistryService {

    private static final long HEALTH_CACHE_TTL_SECONDS = 5;
    private static final int SUPPORTED_CONTRACT_VERSION = 1;
    private static final int SELF_SERVICE_EMAIL_INSTANCE_LIMIT = 5;
    private static final String EMAIL_AGENT_TYPE = "email-agent";

    private final GatewayProperties props;
    private final AgentInstanceRepository instances;
    private final AgentInstanceGrantRepository grants;
    private final RestClient restClient;
    private final ObjectMapper objectMapper;
    /**
     * Per-instance summary, briefly cached.
     *
     * Building one summary costs three separate upstream calls (drafts, today's
     * cost, setup progress), and the listing builds one per instance — so the
     * page every user lands on was O(instances x 3) round trips, serially, on
     * every single view. These are counters on a dashboard, not decisions: a few
     * seconds stale is invisible, and the alternative is a page that gets slower
     * with every mailbox onboarded.
     */
    private final java.util.concurrent.ConcurrentHashMap<String, SummaryCacheEntry> summaryCache =
            new java.util.concurrent.ConcurrentHashMap<>();

    private record SummaryCacheEntry(Map<String, Object> summary, Instant expiresAt) {}

    private final java.util.concurrent.ConcurrentHashMap<String, HealthCacheEntry> healthCache =
            new java.util.concurrent.ConcurrentHashMap<>();

    private record HealthCacheEntry(String health, Instant expiresAt) {}

    /**
     * The agent's own declaration of what it is, briefly cached.
     *
     * A manifest changes when a container is redeployed, so a short TTL is enough
     * and it keeps a listing from paying one extra round trip per instance. When
     * the agent is unreachable or answers with a contract version we do not know,
     * the configured {@code agent-types} entry is served unchanged — an agent that
     * is down must not blank out the settings navigation.
     */
    private final java.util.concurrent.ConcurrentHashMap<String, ManifestCacheEntry> manifestCache =
            new java.util.concurrent.ConcurrentHashMap<>();

    private record ManifestCacheEntry(GatewayProperties.AgentType type, Instant expiresAt) {}

    public AgentRegistryService(GatewayProperties props, AgentInstanceRepository instances,
                                AgentInstanceGrantRepository grants, RestClient.Builder builder,
                                ObjectMapper objectMapper) {
        this.props = props;
        this.instances = instances;
        this.grants = grants;
        this.restClient = builder.build();
        this.objectMapper = objectMapper;
    }

    public List<GatewayProperties.AgentType> agentTypes() {
        return props.getAgentTypes();
    }

    public Optional<GatewayProperties.AgentType> findType(String id) {
        return props.getAgentTypes().stream().filter(t -> t.getId().equals(id)).findFirst();
    }

    /**
     * The configured type overlaid with what the agent says about itself.
     *
     * Callers that route or authorize must keep using {@link #findType} — this is
     * for description only (what the type is called, what it can do, which
     * settings sections exist).
     */
    public GatewayProperties.AgentType describedType(GatewayProperties.AgentType type) {
        int ttl = props.getUpstream().getManifestCacheSeconds();
        ManifestCacheEntry cached = manifestCache.get(type.getId());
        if (ttl > 0 && cached != null && cached.expiresAt().isAfter(Instant.now())) {
            return cached.type();
        }
        GatewayProperties.AgentType resolved = fetchManifest(type).orElse(type);
        if (ttl > 0) {
            manifestCache.put(type.getId(),
                    new ManifestCacheEntry(resolved, Instant.now().plusSeconds(ttl)));
        }
        return resolved;
    }

    private Optional<GatewayProperties.AgentType> fetchManifest(GatewayProperties.AgentType type) {
        if (type.getManifestPath() == null || type.getManifestPath().isBlank()) {
            return Optional.empty();
        }
        try {
            String body = restClient.get()
                    .uri(URI.create(upstreamBase(type) + type.getManifestPath()))
                    .retrieve()
                    .body(String.class);
            JsonNode manifest = objectMapper.readTree(body == null ? "{}" : body);
            if (manifest.path("contract_version").asInt(0) != SUPPORTED_CONTRACT_VERSION) {
                // A newer agent than this gateway. Falling back is the safe answer:
                // rendering half a schema we do not understand is worse than the
                // configured one we do.
                return Optional.empty();
            }
            // The manifest declares the type it *is*; it cannot rename itself into
            // another registered type and inherit that type's routing.
            if (!type.getId().equals(manifest.path("id").asText(""))) {
                return Optional.empty();
            }
            List<String> capabilities = new ArrayList<>();
            manifest.path("capabilities").forEach(node -> capabilities.add(node.asText()));
            List<GatewayProperties.SettingSection> sections = new ArrayList<>();
            for (JsonNode node : manifest.path("settings_schema")) {
                String key = node.path("key").asText("");
                String path = node.path("path").asText("");
                // A settings path is rendered as a workspace link. Anything that is
                // not a plain relative path could point the UI off-origin.
                if (key.isBlank() || !path.startsWith("/") || path.startsWith("//")) {
                    continue;
                }
                sections.add(new GatewayProperties.SettingSection(key,
                        node.path("label").asText(key),
                        node.path("description").asText(""),
                        path));
            }
            return Optional.of(type.withManifest(
                    manifest.path("display_name").asText(""),
                    manifest.path("description").asText(""),
                    capabilities,
                    sections));
        } catch (UnknownAgentTypeException | RestClientException ex) {
            return Optional.empty();
        } catch (Exception ex) {
            return Optional.empty();
        }
    }

    /** Fresh probe on every call — /agents keeps its live health semantics. */
    public String serviceHealth(GatewayProperties.AgentType type) {
        String health = probeServiceHealth(type);
        healthCache.put(type.getId(), new HealthCacheEntry(health, Instant.now().plusSeconds(HEALTH_CACHE_TTL_SECONDS)));
        return health;
    }

    /** Instance listings probe the same upstream once per instance; a short TTL
     *  collapses those duplicate probes without hiding a real outage for long. */
    public String cachedServiceHealth(GatewayProperties.AgentType type) {
        HealthCacheEntry cached = healthCache.get(type.getId());
        if (cached != null && cached.expiresAt().isAfter(Instant.now())) {
            return cached.health();
        }
        return serviceHealth(type);
    }

    private String probeServiceHealth(GatewayProperties.AgentType type) {
        try {
            var response = restClient.get()
                    .uri(URI.create(upstreamBase(type) + type.getHealthPath()))
                    .retrieve()
                    .toBodilessEntity();
            return response.getStatusCode().is2xxSuccessful() ? "healthy" : "unhealthy";
        } catch (UnknownAgentTypeException ex) {
            return "unconfigured";
        } catch (RestClientException ex) {
            return "unreachable";
        }
    }

    public Optional<AgentInstance> findInstance(String id) {
        return instances.findById(id);
    }

    public List<AgentInstance> allInstances() {
        return instances.findAll();
    }

    private static final Set<String> UNHEALTHY_COMPONENT_STATUSES = Set.of("down");
    private static final List<String> HEALTH_COMPONENT_KEYS = List.of("agent", "poller", "security", "database", "redis");

    public record InstanceAuditResult(
            String instanceId, String displayName, boolean reachable,
            Map<String, String> componentStatuses, int dlqPendingCount, List<String> warnings
    ) {}

    /**
     * One instance's contribution to a platform-wide audit: component health plus
     * unresolved DLQ count. Never throws — an unreachable instance comes back as
     * its own bad row (`reachable=false`), not a failed audit for every instance.
     */
    public InstanceAuditResult auditProbe(AgentInstance instance) {
        List<String> warnings = new ArrayList<>();
        if ("inactive".equalsIgnoreCase(instance.getStatus())) {
            return new InstanceAuditResult(instance.getId(), instance.getDisplayName(), true, Map.of(), 0, warnings);
        }
        Map<String, String> componentStatuses = new LinkedHashMap<>();
        try {
            JsonNode health = getJson(instance, "platform-audit", "/health");
            for (String key : HEALTH_COMPONENT_KEYS) {
                String status = health.path(key).path("status").asText("unknown");
                componentStatuses.put(key, status);
                if (UNHEALTHY_COMPONENT_STATUSES.contains(status)) {
                    warnings.add(key + " is down");
                } else if ("paused".equals(status)) {
                    warnings.add(key + " is paused");
                }
            }
        } catch (RuntimeException ex) {
            return new InstanceAuditResult(instance.getId(), instance.getDisplayName(), false, Map.of(), 0,
                    List.of("instance unreachable: " + safeMessage(ex)));
        }
        int dlqPending = 0;
        try {
            JsonNode dlq = getJson(instance, "platform-audit", "/dlq?status=dead_letter&limit=500");
            dlqPending = dlq.path("entries").isArray() ? dlq.path("entries").size() : 0;
            if (dlqPending > 0) {
                warnings.add(dlqPending + " message(s) stuck in the dead-letter queue");
            }
        } catch (RuntimeException ex) {
            warnings.add("could not read DLQ: " + safeMessage(ex));
        }
        return new InstanceAuditResult(instance.getId(), instance.getDisplayName(), true, componentStatuses, dlqPending, warnings);
    }

    private static String safeMessage(RuntimeException ex) {
        return ex.getMessage() == null ? ex.getClass().getSimpleName() : ex.getMessage();
    }

    public List<AgentInstance> visibleInstances(String username, String role) {
        return instances.findAll().stream()
                .filter(instance -> canView(instance, username, role))
                .filter(instance -> !"inactive".equalsIgnoreCase(instance.getStatus())
                        || "owner".equals(role)
                        || "admin".equals(role))
                .toList();
    }

    public boolean canView(AgentInstance instance, String username, String role) {
        if ("admin".equals(role)) return true;
        if (username != null && username.equals(instance.getCreatedBy())) return true;
        if (username != null && grants.findByAgentInstanceIdAndUserId(instance.getId(), username)
                .filter(InstanceGrantService::active)
                .isPresent()) return true;
        return Arrays.stream(instance.getAllowedRoles().split(","))
                .map(String::trim)
                .anyMatch(r -> r.equalsIgnoreCase(role));
    }

    public AgentInstance create(CreateAgentInstanceRequest request, String username, String role) {
        GatewayProperties.AgentType type = findType(request.agentType())
                .orElseThrow(() -> new UnknownAgentTypeException(request.agentType()));
        boolean admin = "admin".equals(role);
        if (request.allowedRoles() != null && containsAdminRole(request.allowedRoles())) {
            // allowed_roles grants standing, unexpiring access to every account
            // holding that global role — the opposite of the 24h cap grants.addGrant()
            // enforces for admin-targeted grants. Letting "admin" into this list would
            // silently reopen that hole for every current and future admin.
            throw new InvalidAllowedRolesException();
        }
        if (!admin) {
            if (!EMAIL_AGENT_TYPE.equals(type.getId())) {
                throw new ForbiddenAgentInstanceOperationException("users can only create email-agent instances");
            }
            long ownedEmailAgents = instances.countByCreatedByAndAgentType(username, EMAIL_AGENT_TYPE);
            if (ownedEmailAgents >= SELF_SERVICE_EMAIL_INSTANCE_LIMIT) {
                throw new AgentInstanceLimitException(SELF_SERVICE_EMAIL_INSTANCE_LIMIT);
            }
        }
        String id = request.id() == null || request.id().isBlank()
                ? slug(request.displayName()) + "-" + UUID.randomUUID().toString().substring(0, 8)
                : slug(request.id());
        if (instances.existsById(id)) {
            throw new DuplicateAgentInstanceException(id);
        }
        Optional<AgentInstance> existingMailbox = activeInstanceUsingMailbox(type.getId(), request.mailboxIdentity());
        if (existingMailbox.isPresent()) {
            throw new DuplicateMailboxIdentityException(request.mailboxIdentity(), existingMailbox.get());
        }
        AgentInstance instance = new AgentInstance(
                id,
                type.getId(),
                request.displayName(),
                request.mailboxIdentity(),
                request.description(),
                request.status() == null || request.status().isBlank() ? "active" : request.status(),
                type.getBasePath(),
                // Private by default. allowed_roles grants access to every account
                // holding that global role, so defaulting it to "owner" meant a
                // second owner-role user could read this mailbox by naming the
                // instance in X-Agora-Agent-Instance — verified reading another
                // tenant's real inbox. Only an admin may deliberately widen it;
                // everyone else gets creator + explicit grants + admin.
                admin && request.allowedRoles() != null && !request.allowedRoles().isBlank() ? request.allowedRoles() : "",
                username,
                request.color() == null || request.color().isBlank() ? type.getColor() : request.color(),
                request.icon() == null || request.icon().isBlank() ? type.getIcon() : request.icon()
        );
        AgentInstance saved = instances.save(instance);
        String assignedTo = admin ? request.assignedTo() : null;
        if (assignedTo != null && !assignedTo.isBlank() && !assignedTo.equals(username)) {
            grants.save(new AgentInstanceGrant(saved.getId(), assignedTo, "owner", username));
        }
        return saved;
    }

    private Optional<AgentInstance> activeInstanceUsingMailbox(String agentType, String mailboxIdentity) {
        String normalizedMailbox = normalizeMailbox(mailboxIdentity);
        if (normalizedMailbox.isBlank()) {
            return Optional.empty();
        }
        return instances.findAll().stream()
                .filter(instance -> agentType.equals(instance.getAgentType()))
                .filter(instance -> !"inactive".equalsIgnoreCase(instance.getStatus()))
                .filter(instance -> normalizedMailbox.equals(normalizeMailbox(instance.getMailboxIdentity())))
                .findFirst();
    }

    private static String normalizeMailbox(String mailboxIdentity) {
        return mailboxIdentity == null ? "" : mailboxIdentity.trim().toLowerCase(Locale.ROOT);
    }

    public Map<String, Object> summary(AgentInstance instance, String username) {
        // Keyed by user too: pending drafts and cost are tenant-scoped, so one
        // person's totals must never be served to another.
        long ttl = props.getUpstream().getSummaryCacheSeconds();
        if (ttl <= 0) {
            return buildSummary(instance, username);
        }
        String cacheKey = instance.getId() + "\u0000" + (username == null ? "" : username);
        SummaryCacheEntry cached = summaryCache.get(cacheKey);
        if (cached != null && cached.expiresAt().isAfter(Instant.now())) {
            return cached.summary();
        }
        Map<String, Object> built = buildSummary(instance, username);
        summaryCache.put(cacheKey, new SummaryCacheEntry(built, Instant.now().plusSeconds(ttl)));
        return built;
    }

    /** Drop cached summaries for an instance whose state just changed. */
    public void invalidateSummary(String instanceId) {
        summaryCache.keySet().removeIf(key -> key.startsWith(instanceId + "\u0000"));
    }

    private Map<String, Object> buildSummary(AgentInstance instance, String username) {
        Map<String, Object> summary = new LinkedHashMap<>();
        if ("inactive".equalsIgnoreCase(instance.getStatus())) {
            summary.put("service_health", "inactive");
        } else {
            findType(instance.getAgentType()).ifPresent(type -> summary.put("service_health", cachedServiceHealth(type)));
        }
        if (!"email-agent".equals(instance.getAgentType())) {
            return summary;
        }

        summary.put("mailbox_connection", "unknown");
        summary.put("sync_status", "unknown");
        if ("inactive".equalsIgnoreCase(instance.getStatus())) {
            summary.put("pending_drafts", 0);
            summary.put("today_cost_eur", 0.0);
            summary.put("setup_status", "unknown");
            return summary;
        }
        putConnectionStatus(summary, instance, username);
        summary.put("pending_drafts", safePendingDrafts(instance, username));
        summary.put("today_cost_eur", safeTodayCost(instance, username));
        putSetupStatus(summary, instance, username);
        return summary;
    }

    /**
     * Real connection + run state, not a DB-column guess. mailboxIdentity is only
     * ever set for a legacy pinned instance, so checking it as a proxy for "is the
     * mailbox connected" was wrong for every normal instance — this instead asks
     * the agent the same question {@link #mailboxStatus} already answers
     * correctly, so the instance list and the mailboxes overview agree.
     */
    private void putConnectionStatus(Map<String, Object> summary, AgentInstance instance, String username) {
        try {
            JsonNode root = getJson(instance, username, "/sync/status");
            String connection = root.path("connection_status").asText("unknown");
            boolean paused = root.path("paused").asBoolean(false);
            summary.put("mailbox_connection", connection);
            summary.put("sync_status", "connected".equals(connection) ? (paused ? "paused" : "running") : connection);
        } catch (RuntimeException ex) {
            // Leave the "unknown" defaults already in summary — an unreachable
            // agent must not fail the whole instance listing.
        }
    }

    /** Instance listing must not fail if the agent is unreachable — same
     *  degrade-to-"unknown" contract as the existing health lookup. */
    private void putSetupStatus(Map<String, Object> summary, AgentInstance instance, String username) {
        try {
            JsonNode root = getJson(instance, username, "/instance-setup");
            summary.put("setup_status", root.path("status").asText("unknown"));
            summary.put("setup_percent", root.path("progress").path("percent").asInt(0));
            java.util.List<String> failedSteps = new java.util.ArrayList<>();
            JsonNode steps = root.path("steps");
            if (steps.isArray()) {
                for (JsonNode step : steps) {
                    if ("failed".equals(step.path("status").asText())) {
                        failedSteps.add(step.path("step_key").asText());
                    }
                }
            }
            summary.put("setup_failed_steps", failedSteps);
        } catch (RuntimeException ex) {
            summary.put("setup_status", "unknown");
            summary.put("setup_percent", 0);
            summary.put("setup_failed_steps", java.util.List.of());
        }
    }

    /**
     * Mailbox connection state for the overview console.
     *
     * Degrades per instance rather than per request: a mailbox whose agent is
     * unreachable comes back with `reachable=false` and a reason, so one broken
     * mailbox shows as one bad row instead of failing the whole page.
     */
    public Map<String, Object> mailboxStatus(AgentInstance instance, String username) {
        Map<String, Object> status = new LinkedHashMap<>();
        status.put("provider", "gmail");
        if ("inactive".equalsIgnoreCase(instance.getStatus())) {
            status.put("reachable", false);
            status.put("connection_status", "inactive");
            status.put("pending_drafts", 0);
            return status;
        }
        try {
            JsonNode root = getJson(instance, username, "/sync/status");
            status.put("provider", root.path("provider").asText("gmail"));
            status.put("connection_status", root.path("connection_status").asText("unknown"));
            status.put("sync_mode", root.path("sync_mode").asText(""));
            status.put("paused", root.path("paused").asBoolean(false));
            status.put("last_success_at", nullableText(root.path("last_success_at")));
            status.put("last_failure_at", nullableText(root.path("last_failure_at")));
            status.put("last_error", nullableText(root.path("last_error")));
            status.put("reachable", true);
        } catch (RuntimeException ex) {
            status.put("reachable", false);
            status.put("connection_status", "unknown");
            status.put("error", ex.getMessage() == null ? ex.getClass().getSimpleName() : ex.getMessage());
        }
        status.put("pending_drafts", safePendingDrafts(instance, username));
        return status;
    }

    private static String nullableText(JsonNode node) {
        return node.isMissingNode() || node.isNull() ? null : node.asText();
    }

    private int safePendingDrafts(AgentInstance instance, String username) {
        try {
            JsonNode root = getJson(instance, username, "/drafts");
            JsonNode drafts = root.get("drafts");
            return drafts != null && drafts.isArray() ? drafts.size() : 0;
        } catch (RuntimeException ex) {
            return 0;
        }
    }

    private double safeTodayCost(AgentInstance instance, String username) {
        try {
            JsonNode root = getJson(instance, username, "/costs/summary?period=day");
            JsonNode cost = root.path("totals").path("cost_eur");
            return cost.isNumber() ? cost.asDouble() : 0.0;
        } catch (RuntimeException ex) {
            return 0.0;
        }
    }

    private JsonNode getJson(AgentInstance instance, String username, String path) {
        // Every email-agent route except /health and /metrics is gated behind this
        // shared secret (tenant_context_middleware) — without it every call here
        // 401s and every caller (buildSummary, mailboxStatus, auditProbe) degrades
        // silently to "unknown"/0, which is indistinguishable from a genuinely
        // idle instance. Blank secret is a no-op on the agent side too, so this is
        // safe to send unconditionally.
        String secret = props.getAgentSharedSecret();
        String body = restClient.get()
                .uri(URI.create(upstreamBase(instance.getAgentType()) + path))
                .header("X-Agora-User", username == null ? "" : username)
                .header("X-Agora-Agent-Instance", instance.getId())
                .header("X-Agora-Gateway-Secret", secret == null ? "" : secret)
                .retrieve()
                .body(String.class);
        try {
            return objectMapper.readTree(body == null ? "{}" : body);
        } catch (Exception ex) {
            throw new IllegalStateException("Invalid upstream JSON", ex);
        }
    }

    private String upstreamBase(GatewayProperties.AgentType type) {
        return upstreamBase(type.getId());
    }

    private String upstreamBase(String agentType) {
        if ("email-agent".equals(agentType)) {
            return props.getUpstream().getEmailAgentUrl();
        }
        throw new UnknownAgentTypeException(agentType);
    }

    private String slug(String value) {
        String slug = value == null ? "agent-instance" : value.toLowerCase(Locale.ROOT)
                .replaceAll("[^a-z0-9]+", "-")
                .replaceAll("^-|-$", "");
        return slug.isBlank() ? "agent-instance" : slug;
    }

    public AgentInstance deactivate(String instanceId) {
        AgentInstance instance = instances.findById(instanceId)
                .orElseThrow(() -> new UnknownAgentTypeException(instanceId));
        instance.setStatus("inactive");
        invalidateSummary(instanceId);
        return instances.save(instance);
    }

    public AgentInstance activate(String instanceId) {
        AgentInstance instance = instances.findById(instanceId)
                .orElseThrow(() -> new UnknownAgentTypeException(instanceId));
        instance.setStatus("active");
        invalidateSummary(instanceId);
        return instances.save(instance);
    }

    public AgentInstance update(String instanceId, UpdateAgentInstanceRequest request) {
        AgentInstance instance = instances.findById(instanceId)
                .orElseThrow(() -> new UnknownAgentTypeException(instanceId));
        instance.setDisplayName(request.displayName());
        return instances.save(instance);
    }

    public void delete(String instanceId) {
        AgentInstance instance = instances.findById(instanceId)
                .orElseThrow(() -> new UnknownAgentTypeException(instanceId));
        grants.findByAgentInstanceId(instanceId).forEach(grant -> grants.deleteById(grant.getId()));
        invalidateSummary(instanceId);
        instances.delete(instance);
    }

    public record CreateAgentInstanceRequest(
            @com.fasterxml.jackson.annotation.JsonProperty("id") String id,
            @jakarta.validation.constraints.NotBlank @com.fasterxml.jackson.annotation.JsonProperty("agent_type") String agentType,
            @jakarta.validation.constraints.NotBlank @com.fasterxml.jackson.annotation.JsonProperty("display_name") String displayName,
            @com.fasterxml.jackson.annotation.JsonProperty("mailbox_identity") String mailboxIdentity,
            @com.fasterxml.jackson.annotation.JsonProperty("description") String description,
            @com.fasterxml.jackson.annotation.JsonProperty("status") String status,
            @com.fasterxml.jackson.annotation.JsonProperty("allowed_roles") String allowedRoles,
            @com.fasterxml.jackson.annotation.JsonProperty("color") String color,
            @com.fasterxml.jackson.annotation.JsonProperty("icon") String icon,
            // Who this instance is actually for. Left blank, it belongs to no one but
            // global owner/admin (who see every instance regardless) — an explicit
            // pick here is what grants a specific person ownership of it.
            @com.fasterxml.jackson.annotation.JsonProperty("assigned_to") String assignedTo
    ) {}

    public record UpdateAgentInstanceRequest(
            @jakarta.validation.constraints.NotBlank @com.fasterxml.jackson.annotation.JsonProperty("display_name") String displayName
    ) {}

    public static class UnknownAgentTypeException extends RuntimeException {
        public UnknownAgentTypeException(String agentType) {
            super("unknown agent type: " + agentType);
        }
    }

    public static class DuplicateAgentInstanceException extends RuntimeException {
        public DuplicateAgentInstanceException(String id) {
            super("agent instance already exists: " + id);
        }
    }

    public static class AgentInstanceLimitException extends RuntimeException {
        public AgentInstanceLimitException(int limit) {
            super("email-agent instance limit reached: " + limit);
        }
    }

    public static class DuplicateMailboxIdentityException extends RuntimeException {
        public DuplicateMailboxIdentityException(String mailboxIdentity, AgentInstance instance) {
            super("mailbox " + normalizeMailbox(mailboxIdentity)
                    + " is already connected to instance "
                    + instance.getId() + " (" + instance.getDisplayName() + ")");
        }
    }

    public static class InvalidAllowedRolesException extends RuntimeException {
        public InvalidAllowedRolesException() {
            super("allowed_roles cannot include admin");
        }
    }

    private static boolean containsAdminRole(String allowedRoles) {
        return Arrays.stream(allowedRoles.split(","))
                .map(String::trim)
                .anyMatch(r -> r.equalsIgnoreCase("admin"));
    }

    public static class ForbiddenAgentInstanceOperationException extends RuntimeException {
        public ForbiddenAgentInstanceOperationException(String message) {
            super(message);
        }
    }
}
