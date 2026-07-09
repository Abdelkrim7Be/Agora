package com.agora.gateway.proxy;

import com.agora.gateway.agent.AgentRegistryService;
import com.agora.gateway.agent.InstanceGrantService;
import com.agora.gateway.audit.AuditService;
import com.agora.gateway.config.GatewayProperties;
import com.agora.gateway.user.UserRepository;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.util.StreamUtils;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClient;

import org.springframework.security.authentication.AnonymousAuthenticationToken;

import java.io.IOException;
import java.net.URI;
import java.util.Optional;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@RestController
public class ProxyController {

    private static final String PREFIX = "/api/agent";
    private static final String USER_HEADER = "X-Agora-User";
    private static final String AGENT_INSTANCE_HEADER = "X-Agora-Agent-Instance";
    private static final String INSTANCE_ROLE_HEADER = "X-Agora-Instance-Role";
    private static final Pattern VERB_PATTERN = Pattern.compile("^/api/agent/run/[^/]+/([^/]+)$");

    private static final Set<String> HOP_BY_HOP = Set.of(
            "host", "connection", "content-length", "transfer-encoding",
            "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade",
            USER_HEADER.toLowerCase(), AGENT_INSTANCE_HEADER.toLowerCase(),
            INSTANCE_ROLE_HEADER.toLowerCase()
    );

    // Write-tier paths: owner only.
    private static final Set<String> WRITE_PATH_PREFIXES = Set.of(
            "/api/agent/config", "/api/agent/capabilities", "/api/agent/categories",
            "/api/agent/templates", "/api/agent/contacts", "/api/agent/rules",
            "/api/agent/memory", "/api/agent/style", "/api/agent/costs"
    );

    // Approve-tier paths: owner or approver (instance-granted).
    private static final Set<String> APPROVE_PATH_PREFIXES = Set.of(
            "/api/agent/run", "/api/agent/sync", "/api/agent/inbox"
    );

    private final RestClient restClient;
    private final String upstreamBase;
    private final AuditService auditService;
    private final AgentRegistryService agentRegistryService;
    private final InstanceGrantService grantService;
    private final String defaultAgentInstance;
    private final UserRepository userRepository;

    public ProxyController(GatewayProperties props, RestClient.Builder builder, AuditService auditService,
                           AgentRegistryService agentRegistryService, InstanceGrantService grantService,
                           UserRepository userRepository) {
        this.upstreamBase = props.getUpstream().getEmailAgentUrl();
        this.defaultAgentInstance = props.getDefaultAgentInstance();
        this.restClient = builder.build();
        this.auditService = auditService;
        this.agentRegistryService = agentRegistryService;
        this.grantService = grantService;
        this.userRepository = userRepository;
    }

    @RequestMapping("/api/agent/**")
    public ResponseEntity<byte[]> proxy(HttpServletRequest request) throws IOException {
        String downstreamPath = request.getRequestURI();
        String upstreamPath = downstreamPath.startsWith(PREFIX)
                ? downstreamPath.substring(PREFIX.length())
                : downstreamPath;

        String query = request.getQueryString();
        String upstreamUrl = upstreamBase + upstreamPath + (query != null ? "?" + query : "");

        HttpMethod method = HttpMethod.valueOf(request.getMethod());
        byte[] body = StreamUtils.copyToByteArray(request.getInputStream());
        String contentType = request.getContentType();

        var spec = restClient.method(method).uri(URI.create(upstreamUrl));

        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        boolean authenticated = auth != null && auth.isAuthenticated() && !(auth instanceof AnonymousAuthenticationToken);
        String username = authenticated ? auth.getName() : null;
        String jwtRole = authenticated ? auth.getAuthorities().stream()
                .map(GrantedAuthority::getAuthority)
                .map(a -> a.startsWith("ROLE_") ? a.substring(5).toLowerCase() : a)
                .findFirst().orElse(null) : null;

        // Strip any client-supplied instance header; select and authorize the instance.
        String requestedAgentInstance = request.getHeader(AGENT_INSTANCE_HEADER);
        String agentInstance = selectAgentInstance(requestedAgentInstance, username, jwtRole);
        if (agentInstance == null) {
            auditService.record(username, jwtRole, deriveAction(request), request.getMethod(),
                    downstreamPath, null, "denied");
            return forbidden();
        }

        // Resolve effective instance role for this caller on this instance.
        // Unauthenticated requests (permitAll paths: webhook, oauth callback) skip instance-role
        // enforcement — the gateway's SecurityConfig already controls who reaches the proxy.
        String effectiveRole;
        if (username == null) {
            effectiveRole = "viewer"; // unauthenticated pass-through for permitAll paths
        } else {
            Optional<String> effectiveRoleOpt = grantService.effectiveRole(agentInstance, username, jwtRole);
            if (effectiveRoleOpt.isEmpty()) {
                auditService.record(username, jwtRole, "agent_instance_access", request.getMethod(),
                        downstreamPath, null, "denied");
                return forbidden();
            }
            effectiveRole = effectiveRoleOpt.get();
        }

        // Enforce instance-level authorization tier for the requested path.
        // Skip for unauthenticated requests (SecurityConfig already guards them).
        if (username != null) {
            String tier = deriveTier(downstreamPath, request.getMethod());
            if (!grantService.isAuthorized(effectiveRole, tier)) {
                auditService.record(username, jwtRole, deriveAction(request), request.getMethod(),
                        downstreamPath, null, "denied");
                return forbidden();
            }
        }

        // Forward safe headers (skip hop-by-hop and internally owned identity headers).
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

        if (body.length > 0 && contentType != null) {
            spec = spec.contentType(MediaType.parseMediaType(contentType)).body(body);
        }

        return spec.exchange((req, resp) -> {
            byte[] responseBody = resp.getBody().readAllBytes();
            int status = resp.getStatusCode().value();

            auditService.record(username, jwtRole, deriveAction(request), request.getMethod(),
                    downstreamPath, status, "forwarded");

            return ResponseEntity
                    .status(resp.getStatusCode())
                    .contentType(resp.getHeaders().getContentType() != null
                            ? resp.getHeaders().getContentType()
                            : MediaType.APPLICATION_OCTET_STREAM)
                    .body(responseBody);
        });
    }

    @RequestMapping("/health")
    public ResponseEntity<Object> health() {
        return ResponseEntity.ok(java.util.Map.of("status", "ok"));
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

    private String deriveTier(String path, String method) {
        if ("GET".equals(method)) return "read";
        for (String prefix : WRITE_PATH_PREFIXES) {
            if (path.startsWith(prefix)) return "write";
        }
        for (String prefix : APPROVE_PATH_PREFIXES) {
            if (path.startsWith(prefix)) return "approve";
        }
        // Configuration-style paths not enumerated default to write tier for mutations.
        return "write";
    }

    private String deriveAction(HttpServletRequest request) {
        String path = request.getRequestURI();
        String method = request.getMethod();
        if ("GET".equals(method) && path.startsWith("/api/agent/run")) {
            return "read";
        }
        if ("POST".equals(method) && "/api/agent/run".equals(path)) {
            return "run";
        }
        Matcher m = VERB_PATTERN.matcher(path);
        if ("POST".equals(method) && m.matches()) {
            return m.group(1);
        }
        return method + " " + path;
    }

    private ResponseEntity<byte[]> forbidden() {
        return ResponseEntity.status(403)
                .contentType(MediaType.APPLICATION_JSON)
                .body("{\"error\":\"forbidden\"}".getBytes(java.nio.charset.StandardCharsets.UTF_8));
    }
}
