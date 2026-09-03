package com.agora.gateway.proxy;

import com.agora.gateway.agent.AgentRegistryService;
import com.agora.gateway.agent.InstanceGrantService;
import com.agora.gateway.audit.AuditService;
import com.agora.gateway.config.GatewayProperties;
import com.agora.gateway.user.UserRepository;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.security.authentication.AnonymousAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.util.StreamUtils;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.Optional;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@RestController
public class ProxyController {

    private static final String PREFIX = "/api/agent";
    private static final String USER_HEADER = "X-Agora-User";
    private static final String USER_DEPT_HEADER = "X-Agora-User-Dept";
    private static final String AGENT_INSTANCE_HEADER = "X-Agora-Agent-Instance";
    private static final String INSTANCE_ROLE_HEADER = "X-Agora-Instance-Role";
    private static final String GATEWAY_SECRET_HEADER = "X-Agora-Gateway-Secret";
    private static final Pattern VERB_PATTERN = Pattern.compile("^/api/agent/run/[^/]+/([^/]+)$");

    private static final Set<String> HOP_BY_HOP = Set.of(
            "host", "connection", "content-length", "transfer-encoding",
            "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade",
            USER_HEADER.toLowerCase(), USER_DEPT_HEADER.toLowerCase(), AGENT_INSTANCE_HEADER.toLowerCase(),
            INSTANCE_ROLE_HEADER.toLowerCase(), GATEWAY_SECRET_HEADER.toLowerCase()
    );

    // Write-tier paths: owner only.
    private static final Set<String> WRITE_PATH_PREFIXES = Set.of(
            "/api/agent/config", "/api/agent/capabilities", "/api/agent/categories",
            "/api/agent/templates", "/api/agent/contacts", "/api/agent/rules",
            "/api/agent/memory", "/api/agent/style", "/api/agent/signature", "/api/agent/costs",
            "/api/agent/persona", "/api/agent/send-mode"
    );

    // Approve-tier paths: owner or approver (instance-granted).
    private static final Set<String> APPROVE_PATH_PREFIXES = Set.of(
            "/api/agent/run", "/api/agent/sync"
    );

    // Body cap for proxied requests (uploads included) — the whole body is
    // buffered in memory, so an explicit limit keeps oversized payloads out.
    private static final int MAX_PROXY_BODY_BYTES = 2 * 1024 * 1024;

    private final RestClient restClient;
    private final String upstreamBase;
    private final AuditService auditService;
    private final AgentRegistryService agentRegistryService;
    private final InstanceGrantService grantService;
    private final String defaultAgentInstance;
    private final String agentSharedSecret;
    private final UserRepository userRepository;

    public ProxyController(GatewayProperties props, RestClient.Builder builder, AuditService auditService,
                           AgentRegistryService agentRegistryService, InstanceGrantService grantService,
                           UserRepository userRepository) {
        this.upstreamBase = props.getUpstream().getEmailAgentUrl();
        this.defaultAgentInstance = props.getDefaultAgentInstance();
        this.agentSharedSecret = props.getAgentSharedSecret();
        if ((agentSharedSecret == null || agentSharedSecret.isBlank()) && !isLocalUpstream(upstreamBase)) {
            throw new IllegalStateException("GATEWAY_AGENT_SHARED_SECRET must be set");
        }
        this.restClient = builder.build();
        this.auditService = auditService;
        this.agentRegistryService = agentRegistryService;
        this.grantService = grantService;
        this.userRepository = userRepository;
    }

    @RequestMapping("/api/agent/**")
    public void proxy(HttpServletRequest request, HttpServletResponse response) throws IOException {
        String downstreamPath = request.getRequestURI();
        String upstreamPath = downstreamPath.startsWith(PREFIX)
                ? downstreamPath.substring(PREFIX.length())
                : downstreamPath;

        String query = request.getQueryString();
        String upstreamUrl = upstreamBase + upstreamPath + (query != null ? "?" + query : "");

        HttpMethod method = HttpMethod.valueOf(request.getMethod());
        byte[] body = StreamUtils.copyToByteArray(request.getInputStream());
        String contentType = request.getContentType();

        if (body.length > MAX_PROXY_BODY_BYTES) {
            auditService.record(null, null, deriveAction(request), request.getMethod(),
                    downstreamPath, 413, "denied");
            response.setStatus(413);
            response.setContentType(MediaType.APPLICATION_JSON_VALUE);
            response.getWriter().write("{\"error\":\"payload too large\"}");
            return;
        }

        var spec = restClient.method(method).uri(URI.create(upstreamUrl));

        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        boolean authenticated = auth != null && auth.isAuthenticated() && !(auth instanceof AnonymousAuthenticationToken);
        String username = authenticated ? auth.getName() : null;
        String jwtRole = authenticated ? auth.getAuthorities().stream()
                .map(GrantedAuthority::getAuthority)
                .map(a -> a.startsWith("ROLE_") ? a.substring(5).toLowerCase() : a)
                .findFirst().orElse(null) : null;

        String requestedAgentInstance = request.getHeader(AGENT_INSTANCE_HEADER);
        String agentInstance = selectAgentInstance(requestedAgentInstance, username, jwtRole);
        if (agentInstance == null) {
            recordDeniedAuditIfRelevant(username, jwtRole, deriveAction(request), request, downstreamPath);
            writeForbidden(response);
            return;
        }

        String effectiveRole;
        if (username == null) {
            effectiveRole = "viewer";
        } else {
            Optional<String> effectiveRoleOpt = grantService.effectiveRole(agentInstance, username, jwtRole);
            if (effectiveRoleOpt.isEmpty()) {
                recordDeniedAuditIfRelevant(username, jwtRole, "agent_instance_access", request, downstreamPath);
                writeForbidden(response);
                return;
            }
            effectiveRole = effectiveRoleOpt.get();
        }

        if (username != null) {
            String tier = deriveTier(downstreamPath, request.getMethod());
            if (!grantService.isAuthorized(effectiveRole, tier)) {
                recordDeniedAuditIfRelevant(username, jwtRole, deriveAction(request), request, downstreamPath);
                writeForbidden(response);
                return;
            }
            if (isMailboxContentRead(downstreamPath, request.getMethod())
                    && grantService.contentRole(agentInstance, username).isEmpty()) {
                auditService.record(username, jwtRole, "mailbox_content_access", request.getMethod(),
                        "/agent-instances/" + agentInstance, null, "denied " + deriveAction(request));
                writeForbidden(response);
                return;
            }
        }

        var headerNames = request.getHeaderNames();
        while (headerNames.hasMoreElements()) {
            String name = headerNames.nextElement();
            if (!HOP_BY_HOP.contains(name.toLowerCase())) {
                spec = spec.header(name, request.getHeader(name));
            }
        }
        if (username != null) {
            spec = spec.header(USER_HEADER, username);
            var userOpt = userRepository.findByUsername(username);
            if (userOpt.isPresent() && userOpt.get().getDepartment() != null) {
                spec = spec.header("X-Agora-User-Dept", userOpt.get().getDepartment());
            }
        }
        spec = spec.header(AGENT_INSTANCE_HEADER, agentInstance);
        spec = spec.header(INSTANCE_ROLE_HEADER, effectiveRole);
        if (agentSharedSecret != null && !agentSharedSecret.isBlank()) {
            spec = spec.header(GATEWAY_SECRET_HEADER, agentSharedSecret);
        }

        if (body.length > 0 && contentType != null) {
            spec = spec.contentType(MediaType.parseMediaType(contentType)).body(body);
        }

        try {
            spec.exchange((req, resp) -> {
                int status = resp.getStatusCode().value();

                if (shouldRecordForwardedAudit(request, downstreamPath)) {
                    auditService.record(username, jwtRole, deriveAction(request), request.getMethod(),
                            downstreamPath, status, "forwarded");
                }
                if ("admin".equals(jwtRole) && shouldRecordAdminMailboxAccess(request, downstreamPath)) {
                    auditService.record(username, jwtRole, "admin_mailbox_access", request.getMethod(),
                            "/agent-instances/" + agentInstance, status, deriveAction(request));
                }

                response.setStatus(status);
                MediaType upstreamContentType = resp.getHeaders().getContentType();
                response.setContentType((upstreamContentType != null
                        ? upstreamContentType
                        : MediaType.APPLICATION_OCTET_STREAM).toString());
                resp.getHeaders().forEach((name, values) -> {
                    if (HOP_BY_HOP.contains(name.toLowerCase())) {
                        return;
                    }
                    for (String value : values) {
                        response.addHeader(name, value);
                    }
                });
                StreamUtils.copy(resp.getBody(), response.getOutputStream());
                response.flushBuffer();
                return null;
            });
        } catch (RestClientException e) {
            // .exchange() throws instead of returning when the upstream agent never
            // responds (connect/read timeout) or the connection drops mid-request. Left
            // uncaught, this both skipped the audit write above (a proxied action would
            // leave no trace at all) and surfaced to the caller as a bare 500 with no
            // way to tell a slow agent apart from a real server error.
            if (shouldRecordForwardedAudit(request, downstreamPath)) {
                auditService.record(username, jwtRole, deriveAction(request), request.getMethod(),
                        downstreamPath, null, e instanceof ResourceAccessException ? "timeout" : "upstream_error");
            }
            writeUpstreamError(response, e instanceof ResourceAccessException);
        }
    }

    @RequestMapping("/health")
    public java.util.Map<String, String> health() {
        return java.util.Map.of("status", "ok");
    }

    private String selectAgentInstance(String requested, String username, String role) {
        if (requested == null || requested.isBlank()) return defaultAgentInstance;
        if (username == null) return defaultAgentInstance;
        return agentRegistryService.findInstance(requested)
                .filter(instance -> "active".equals(instance.getStatus()))
                .filter(instance -> agentRegistryService.canView(instance, username, role))
                .map(instance -> requested)
                .orElse(null);
    }

    private boolean isLocalUpstream(String url) {
        return url != null && (url.startsWith("http://localhost:") || url.startsWith("http://127.0.0.1:"));
    }

    private String deriveTier(String path, String method) {
        if ("GET".equals(method)) return "read";
        if ("POST".equals(method) && ("/api/agent/run".equals(path) || "/api/agent/run/stream".equals(path))) return "write";
        if ("POST".equals(method) && (path.endsWith("/claim") || path.endsWith("/assign"))
                && path.startsWith("/api/agent/inbox/")) {
            return "approve";
        }
        if (path.startsWith("/api/agent/inbox")) return "write";
        for (String prefix : WRITE_PATH_PREFIXES) {
            if (path.startsWith(prefix)) return "write";
        }
        for (String prefix : APPROVE_PATH_PREFIXES) {
            if (path.startsWith(prefix)) return "approve";
        }
        return "write";
    }

    private boolean isMailboxContentRead(String path, String method) {
        if (!"GET".equals(method)) return false;
        return path.equals("/api/agent/inbox")
                || path.equals("/api/agent/runs")
                || path.startsWith("/api/agent/run/");
    }

    /**
     * Read-only status endpoints the web client polls on a timer.
     *
     * The audit trail exists to answer "who did what, and who saw whose mail".
     * These answer neither: a health blob, aggregate counters, a connection badge,
     * setup progress, an unread count. None of them names a message, a run or a
     * correspondent. They were nonetheless writing a row every few seconds per
     * open tab, which pushed real actions off the first page of the trail within
     * about two minutes and made it useless for the thing it is for.
     *
     * Anything that discloses a record stays audited: {@code GET /run/{id}},
     * {@code /inbox}, {@code /drafts}, {@code /messages}, {@code /contacts}, and
     * every write. Admin mailbox access is recorded separately and is not
     * affected by this list.
     */
    private static final Set<String> POLLED_STATUS_PATHS = Set.of(
            "/api/agent/runs",
            "/api/agent/health",
            "/api/agent/analytics",
            "/api/agent/metrics",
            "/api/agent/sync/status",
            "/api/agent/instance-setup",
            "/api/agent/events",
            "/api/agent/notifications",
            "/api/agent/notifications/unread-count"
    );

    private boolean shouldRecordForwardedAudit(HttpServletRequest request, String downstreamPath) {
        if (!"GET".equals(request.getMethod())) {
            return true;
        }
        // Match on the path only: a query string carries filters, never authority.
        int queryStart = downstreamPath.indexOf('?');
        String path = queryStart >= 0 ? downstreamPath.substring(0, queryStart) : downstreamPath;
        return !POLLED_STATUS_PATHS.contains(path);
    }

    private void recordDeniedAuditIfRelevant(String username, String jwtRole, String action,
                                             HttpServletRequest request, String downstreamPath) {
        if (shouldRecordForwardedAudit(request, downstreamPath)) {
            auditService.record(username, jwtRole, action, request.getMethod(), downstreamPath, null, "denied");
        }
    }

    private boolean shouldRecordAdminMailboxAccess(HttpServletRequest request, String downstreamPath) {
        // Same exclusion as the forwarded trail. "An administrator opened your
        // mailbox" has to mean something; a row every few seconds for the tab's
        // own unread-count poll made the notice read as constant surveillance
        // and buried the one access that mattered.
        if (!shouldRecordForwardedAudit(request, downstreamPath)) {
            return false;
        }
        return downstreamPath.startsWith("/api/agent/");
    }

    private String deriveAction(HttpServletRequest request) {
        String path = request.getRequestURI();
        String method = request.getMethod();
        if ("GET".equals(method) && path.startsWith("/api/agent/run")) {
            return "read";
        }
        if ("POST".equals(method) && ("/api/agent/run".equals(path) || "/api/agent/run/stream".equals(path))) {
            return "run";
        }
        Matcher m = VERB_PATTERN.matcher(path);
        if ("POST".equals(method) && m.matches()) {
            return m.group(1);
        }
        return method + " " + path;
    }

    private void writeForbidden(HttpServletResponse response) throws IOException {
        response.setStatus(403);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.getOutputStream().write("{\"error\":\"forbidden\"}".getBytes(StandardCharsets.UTF_8));
        response.flushBuffer();
    }

    private void writeUpstreamError(HttpServletResponse response, boolean isTimeout) throws IOException {
        response.setStatus(isTimeout ? 504 : 502);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        String body = isTimeout
                ? "{\"error\":\"upstream_timeout\"}"
                : "{\"error\":\"upstream_unavailable\"}";
        response.getOutputStream().write(body.getBytes(StandardCharsets.UTF_8));
        response.flushBuffer();
    }
}
