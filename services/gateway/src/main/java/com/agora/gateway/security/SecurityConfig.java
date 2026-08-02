package com.agora.gateway.security;

import java.util.Arrays;
import java.util.List;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.CorsConfigurationSource;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;

@Configuration
@EnableWebSecurity
public class SecurityConfig {

    private final JwtAuthFilter jwtAuthFilter;
    private final RestAuthEntryPoint restAuthEntryPoint;
    private final RestAccessDeniedHandler restAccessDeniedHandler;

    public SecurityConfig(JwtAuthFilter jwtAuthFilter,
                          RestAuthEntryPoint restAuthEntryPoint,
                          RestAccessDeniedHandler restAccessDeniedHandler) {
        this.jwtAuthFilter = jwtAuthFilter;
        this.restAuthEntryPoint = restAuthEntryPoint;
        this.restAccessDeniedHandler = restAccessDeniedHandler;
    }

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http
            .cors(Customizer.withDefaults())
            .csrf(AbstractHttpConfigurer::disable)
            .httpBasic(AbstractHttpConfigurer::disable)
            .formLogin(AbstractHttpConfigurer::disable)
            .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
            .authorizeHttpRequests(auth -> auth
                .requestMatchers(HttpMethod.GET, "/health").permitAll()
                .requestMatchers(HttpMethod.POST, "/auth/login").permitAll()
                .requestMatchers(HttpMethod.POST, "/auth/refresh").permitAll()
                .requestMatchers(HttpMethod.POST, "/auth/logout").authenticated()
                // /auth/mfa/verify is the second leg of login: the caller holds only the
                // short-lived challenge token from /auth/login (in the body, not a Bearer
                // header — JwtAuthFilter never even looks at it), not a real access token yet.
                .requestMatchers(HttpMethod.POST, "/auth/mfa/verify").permitAll()
                .requestMatchers(HttpMethod.POST, "/auth/mfa/setup").authenticated()
                .requestMatchers(HttpMethod.POST, "/auth/mfa/confirm").authenticated()
                .requestMatchers(HttpMethod.POST, "/auth/mfa/disable").authenticated()
                .requestMatchers(HttpMethod.GET, "/users").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/users").hasRole("ADMIN")
                .requestMatchers(HttpMethod.PUT, "/users/*").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/users/*/disable").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/users/*/enable").hasRole("ADMIN")
                .requestMatchers(HttpMethod.GET, "/agents").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/agent-instances").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/agent-instances").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/agent-instances/*").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/agent-instances/*/activate").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/agent-instances/*/deactivate").hasRole("ADMIN")
                .requestMatchers(HttpMethod.GET, "/agent-instances/*/grants").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/agent-instances/*/grants").hasRole("ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/agent-instances/*/grants/*").hasRole("ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/agent-instances/*").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/disconnect/gmail").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/style").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/memory").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/webhooks/gmail").permitAll()
                .requestMatchers(HttpMethod.GET, "/api/agent/connect/gmail/callback").permitAll()
                .requestMatchers(HttpMethod.GET, "/api/agent/agent-instances/*/connect/gmail/start").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers("/api/agent/campaigns/**").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/run").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/run/stream").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                // approve/reject/respond: VIEWER stays in the gate so a user whose global JWT
                // role is viewer but holds a per-instance approver grant can still reach
                // ProxyController, which enforces the real (instance-level) authorization.
                // APPROVER is now a first-class JWT role (wave-1b) for users who are globally
                // approvers, not just grant-delegated.
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/approve").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/reject").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/respond").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/respond/stream").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                // Read-only AI helpers (thread summary, tone-adjust preview) — same access
                // as the rest of the approval surface; neither mutates run state.
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/summarize").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/tone").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/sync").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/sync/status").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/sync/pause").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/sync/resume").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/costs").hasRole("ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/costs/**").hasRole("ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/style").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/style").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/style/**").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/runs").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/events").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/runs/bulk").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/inbox").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/archive").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/trash").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/read").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/unread").hasAnyRole("OWNER", "ADMIN")
                // Re-queueing a message for the agent re-runs the pipeline on it, so it
                // sits with the other mailbox mutations rather than the read surface.
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/force-agent").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/signature").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/signature").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/signature/image").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/signature/image").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/signature/image").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/signature/apply").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/memory").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/memory").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/memory/summary").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/memory/item").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/config").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/config").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/send-mode").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/send-mode").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/persona").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/persona").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/persona/suggest").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/capabilities").hasRole("ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/capabilities").hasRole("ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/policy").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                // Distinct from the top-level /health (gateway's own liveness, permitAll,
                // used by the Docker healthcheck unauthenticated). This is the proxied
                // email-agent System Health aggregate for the browser control panel.
                .requestMatchers(HttpMethod.GET, "/api/agent/health").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/metrics").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/dlq").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/dlq/*/requeue").hasRole("ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/roles").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/roles").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/roles/*").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/roles/*").hasAnyRole("OWNER", "ADMIN")
                // Category proposals from the mailbox scan: readable alongside the
                // categories themselves, but only an owner turns one into a category.
                // Declared before the generic /categories/* rules so the literal
                // "proposals" path never falls through to them.
                .requestMatchers(HttpMethod.GET, "/api/agent/categories/proposals").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/categories/proposals/accept").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/categories/proposals/dismiss").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/categories").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/categories").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/categories/*").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/categories/*").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/categories/test-match").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/categories/*/duplicate").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/templates").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/contacts").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/contacts").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/contacts/*").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/contacts/*").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/contacts/import").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/contacts/categorize").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/contacts/migrate-legacy").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/contacts/*/photo").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/contacts/*/photo").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/contacts/*/photo").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/segments").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/segments").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/segments/*").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/segments/*").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/drafts").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                // Junk-gate settings: readable by anyone who can see the workspace,
                // editable by the instance owner, same shape as the rules surface.
                // Block candidates derived from the mailbox: readable with the settings.
                .requestMatchers(HttpMethod.GET, "/api/agent/junk/suggestions").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/junk").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/junk").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/rules").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/rules").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/rules/suggestions").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                // Starter rules offered at onboarding; only an owner accepts one.
                .requestMatchers(HttpMethod.GET, "/api/agent/rules/starter").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/rules/starter/apply").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/rules/suggestions/*/promote").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/rules/rule-toggle").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/rules/section-toggle").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/rules/rule").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/rules/rule-delete").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/rules/section-config").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/rules/suggestions/*").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/analytics").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/alerts/settings").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/alerts/settings").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/retention/settings").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/retention/settings").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/retention/dry-run").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/retention/run").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/claim").hasAnyRole("OWNER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/assign").hasAnyRole("OWNER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/run/**").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/instance-setup").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/instance-setup/start").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/instance-setup/retry").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/instance-setup/steps/*/retry").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/instance-setup/skip").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/notifications").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/notifications/unread-count").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/notifications/read-all").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/notifications/*/read").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/notifications/*").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/audit").hasRole("ADMIN")
                .requestMatchers(HttpMethod.GET, "/audit/verify").hasRole("ADMIN")
                // Default-deny: anything not explicitly allowed above is rejected, so a future
                // unenumerated agent route is never reachable by accident.
                .anyRequest().denyAll())
            .exceptionHandling(eh -> eh
                .authenticationEntryPoint(restAuthEntryPoint)
                .accessDeniedHandler(restAccessDeniedHandler))
            .addFilterBefore(jwtAuthFilter, UsernamePasswordAuthenticationFilter.class);
        return http.build();
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    // CORS so the browser control panel (served from a different origin) can call the
    // gateway. Origins are env-driven (GATEWAY_CORS_ALLOWED_ORIGINS, comma-separated;
    // default "*" for dev — tighten in production). Refresh tokens use an HttpOnly cookie,
    // so credentialed CORS is enabled; production must keep an explicit origin allowlist.
    @Bean
    public CorsConfigurationSource corsConfigurationSource(
            @Value("${GATEWAY_CORS_ALLOWED_ORIGINS:*}") String allowedOrigins) {
        CorsConfiguration config = new CorsConfiguration();
        config.setAllowedOriginPatterns(Arrays.asList(allowedOrigins.split(",")));
        config.setAllowCredentials(true);
        config.setAllowedMethods(List.of("GET", "POST", "PUT", "DELETE", "OPTIONS"));
        config.setAllowedHeaders(List.of("Authorization", "Content-Type", "X-Agora-Agent-Instance"));
        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/**", config);
        return source;
    }

    // Prevent Spring Boot from also auto-registering the JWT filter on the plain
    // servlet chain; it should run only inside the security filter chain above.
    @Bean
    public FilterRegistrationBean<JwtAuthFilter> jwtAuthFilterRegistration(JwtAuthFilter filter) {
        FilterRegistrationBean<JwtAuthFilter> registration = new FilterRegistrationBean<>(filter);
        registration.setEnabled(false);
        return registration;
    }
}
