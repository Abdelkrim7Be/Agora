package com.agora.gateway.security;

import com.agora.gateway.audit.AuditService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.access.AccessDeniedHandler;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Component
public class RestAccessDeniedHandler implements AccessDeniedHandler {

    private static final Pattern VERB_PATTERN = Pattern.compile("^/api/agent/run/[^/]+/([^/]+)$");

    private final AuditService auditService;

    public RestAccessDeniedHandler(AuditService auditService) {
        this.auditService = auditService;
    }

    @Override
    public void handle(HttpServletRequest request,
                       HttpServletResponse response,
                       AccessDeniedException accessDeniedException) throws IOException {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        String username = auth != null ? auth.getName() : null;
        String role = auth != null ? auth.getAuthorities().stream()
                .map(GrantedAuthority::getAuthority)
                .map(a -> a.startsWith("ROLE_") ? a.substring(5).toLowerCase() : a)
                .findFirst().orElse(null) : null;

        String path = request.getRequestURI();
        auditService.record(username, role, deriveAction(request), request.getMethod(),
                path, null, "denied");

        response.setStatus(HttpServletResponse.SC_FORBIDDEN);
        response.setContentType("application/json");
        response.getWriter().write("{\"error\":\"forbidden\"}");
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
