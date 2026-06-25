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
                .requestMatchers(HttpMethod.GET, "/agents").hasAnyRole("OWNER", "VIEWER")
                .requestMatchers(HttpMethod.GET, "/agent-instances").hasAnyRole("OWNER", "VIEWER")
                .requestMatchers(HttpMethod.POST, "/agent-instances").hasRole("OWNER")
                .requestMatchers(HttpMethod.POST, "/api/agent/webhooks/gmail").permitAll()
                .requestMatchers(HttpMethod.GET, "/api/agent/connect/gmail/callback").permitAll()
                .requestMatchers(HttpMethod.GET, "/api/agent/agent-instances/*/connect/gmail/start").hasRole("OWNER")
                .requestMatchers(HttpMethod.POST, "/api/agent/run").hasRole("OWNER")
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/approve").hasRole("OWNER")
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/reject").hasRole("OWNER")
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/respond").hasRole("OWNER")
                .requestMatchers(HttpMethod.POST, "/api/agent/sync").hasRole("OWNER")
                .requestMatchers(HttpMethod.GET, "/api/agent/costs").hasRole("OWNER")
                .requestMatchers(HttpMethod.GET, "/api/agent/costs/**").hasRole("OWNER")
                .requestMatchers(HttpMethod.GET, "/api/agent/style").hasRole("OWNER")
                .requestMatchers(HttpMethod.PUT, "/api/agent/style").hasRole("OWNER")
                .requestMatchers(HttpMethod.POST, "/api/agent/style/**").hasRole("OWNER")
                .requestMatchers(HttpMethod.GET, "/api/agent/runs").hasAnyRole("OWNER", "VIEWER")
                .requestMatchers(HttpMethod.GET, "/api/agent/inbox").hasAnyRole("OWNER", "VIEWER")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/archive").hasRole("OWNER")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/trash").hasRole("OWNER")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/read").hasRole("OWNER")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/unread").hasRole("OWNER")
                .requestMatchers(HttpMethod.GET, "/api/agent/memory").hasAnyRole("OWNER", "VIEWER")
                .requestMatchers(HttpMethod.PUT, "/api/agent/memory").hasRole("OWNER")
                .requestMatchers(HttpMethod.GET, "/api/agent/config").hasAnyRole("OWNER", "VIEWER")
                .requestMatchers(HttpMethod.PUT, "/api/agent/config").hasRole("OWNER")
                .requestMatchers(HttpMethod.GET, "/api/agent/capabilities").hasAnyRole("OWNER", "VIEWER")
                .requestMatchers(HttpMethod.PUT, "/api/agent/capabilities").hasRole("OWNER")
                .requestMatchers(HttpMethod.GET, "/api/agent/policy").hasAnyRole("OWNER", "VIEWER")
                .requestMatchers(HttpMethod.GET, "/api/agent/categories").hasAnyRole("OWNER", "VIEWER")
                .requestMatchers(HttpMethod.PUT, "/api/agent/categories").hasRole("OWNER")
                .requestMatchers(HttpMethod.GET, "/api/agent/templates").hasAnyRole("OWNER", "VIEWER")
                .requestMatchers(HttpMethod.GET, "/api/agent/contacts").hasAnyRole("OWNER", "VIEWER")
                .requestMatchers(HttpMethod.GET, "/api/agent/drafts").hasAnyRole("OWNER", "VIEWER")
                .requestMatchers(HttpMethod.GET, "/api/agent/rules").hasAnyRole("OWNER", "VIEWER")
                .requestMatchers(HttpMethod.PUT, "/api/agent/rules").hasRole("OWNER")
                .requestMatchers(HttpMethod.GET, "/api/agent/run/**").hasAnyRole("OWNER", "VIEWER")
                .requestMatchers(HttpMethod.GET, "/audit").hasRole("OWNER")
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
