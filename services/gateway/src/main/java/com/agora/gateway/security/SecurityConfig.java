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
                .requestMatchers(HttpMethod.GET, "/users").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/users").hasRole("ADMIN")
                .requestMatchers(HttpMethod.PUT, "/users/*").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/users/*/disable").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/users/*/enable").hasRole("ADMIN")
                .requestMatchers(HttpMethod.GET, "/agents").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/agent-instances").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/agent-instances").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/agent-instances/*").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/agent-instances/*/activate").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/agent-instances/*/deactivate").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/agent-instances/*/grants").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/agent-instances/*/grants").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/agent-instances/*/grants/*").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/agent-instances/*").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/disconnect/gmail").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/style").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/memory").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/webhooks/gmail").permitAll()
                .requestMatchers(HttpMethod.GET, "/api/agent/connect/gmail/callback").permitAll()
                .requestMatchers(HttpMethod.GET, "/api/agent/agent-instances/*/connect/gmail/start").hasAnyRole("OWNER", "ADMIN")
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
                .requestMatchers(HttpMethod.POST, "/api/agent/sync").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/sync/status").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/sync/pause").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/sync/resume").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/costs").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/costs/**").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/style").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/style").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/style/**").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/runs").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/runs/bulk").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/inbox").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/archive").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/trash").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/read").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/unread").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/signature").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/signature").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/signature/image").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/signature/image").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/signature/image").hasAnyRole("OWNER", "ADMIN")
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
                .requestMatchers(HttpMethod.GET, "/api/agent/capabilities").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/capabilities").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/policy").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                // Distinct from the top-level /health (gateway's own liveness, permitAll,
                // used by the Docker healthcheck unauthenticated). This is the proxied
                // email-agent System Health aggregate for the browser control panel.
                .requestMatchers(HttpMethod.GET, "/api/agent/health").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/metrics").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/dlq").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/dlq/*/requeue").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/roles").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/roles").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/roles/*").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/roles/*").hasAnyRole("OWNER", "ADMIN")
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
                .requestMatchers(HttpMethod.GET, "/api/agent/contacts/*/photo").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/contacts/*/photo").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/contacts/*/photo").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/segments").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/segments").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/segments/*").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/segments/*").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/drafts").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/rules").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/rules").hasAnyRole("OWNER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/rules/suggestions").hasAnyRole("OWNER", "VIEWER", "ADMIN")
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
                .requestMatchers(HttpMethod.GET, "/audit").hasAnyRole("OWNER", "ADMIN")
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
    // default "*" for dev — tighten in production). No cookies are used (bearer token
    // in the Authorization header), so credentials stay disabled.
    @Bean
    public CorsConfigurationSource corsConfigurationSource(
            @Value("${GATEWAY_CORS_ALLOWED_ORIGINS:*}") String allowedOrigins) {
        CorsConfiguration config = new CorsConfiguration();
        config.setAllowedOrigins(Arrays.asList(allowedOrigins.split(",")));
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
