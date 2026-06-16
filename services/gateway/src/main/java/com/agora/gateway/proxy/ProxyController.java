package com.agora.gateway.proxy;

import com.agora.gateway.audit.AuditService;
import com.agora.gateway.config.GatewayProperties;
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

import java.io.IOException;
import java.net.URI;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@RestController
public class ProxyController {

    private static final String PREFIX = "/api/agent";
    private static final String USER_HEADER = "X-Agora-User";
    private static final Pattern VERB_PATTERN = Pattern.compile("^/api/agent/run/[^/]+/([^/]+)$");

    private static final Set<String> HOP_BY_HOP = Set.of(
            "host", "connection", "content-length", "transfer-encoding",
            "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade",
            USER_HEADER.toLowerCase()
    );

    private final RestClient restClient;
    private final String upstreamBase;
    private final AuditService auditService;

    public ProxyController(GatewayProperties props, RestClient.Builder builder, AuditService auditService) {
        this.upstreamBase = props.getUpstream().getEmailAgentUrl();
        this.restClient = builder.build();
        this.auditService = auditService;
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
        String username = auth != null ? auth.getName() : null;
        String role = auth != null ? auth.getAuthorities().stream()
                .map(GrantedAuthority::getAuthority)
                .map(a -> a.startsWith("ROLE_") ? a.substring(5).toLowerCase() : a)
                .findFirst().orElse(null) : null;

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
        }

        if (body.length > 0 && contentType != null) {
            spec = spec.contentType(MediaType.parseMediaType(contentType)).body(body);
        }

        return spec.exchange((req, resp) -> {
            byte[] responseBody = resp.getBody().readAllBytes();
            int status = resp.getStatusCode().value();

            auditService.record(username, role, deriveAction(request), request.getMethod(),
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
}
