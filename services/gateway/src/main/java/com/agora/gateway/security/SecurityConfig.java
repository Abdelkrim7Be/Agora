package com.agora.gateway.security;

import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;

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
            .csrf(AbstractHttpConfigurer::disable)
            .httpBasic(AbstractHttpConfigurer::disable)
            .formLogin(AbstractHttpConfigurer::disable)
            .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
            .authorizeHttpRequests(auth -> auth
                .requestMatchers(HttpMethod.GET, "/health").permitAll()
                .requestMatchers(HttpMethod.POST, "/auth/login").permitAll()
                .requestMatchers(HttpMethod.POST, "/api/agent/run").hasRole("OWNER")
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/approve").hasRole("OWNER")
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/reject").hasRole("OWNER")
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/respond").hasRole("OWNER")
                .requestMatchers(HttpMethod.GET, "/api/agent/runs").hasAnyRole("OWNER", "VIEWER")
                .requestMatchers(HttpMethod.GET, "/api/agent/memory").hasAnyRole("OWNER", "VIEWER")
                .requestMatchers(HttpMethod.PUT, "/api/agent/memory").hasRole("OWNER")
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

    // Prevent Spring Boot from also auto-registering the JWT filter on the plain
    // servlet chain; it should run only inside the security filter chain above.
    @Bean
    public FilterRegistrationBean<JwtAuthFilter> jwtAuthFilterRegistration(JwtAuthFilter filter) {
        FilterRegistrationBean<JwtAuthFilter> registration = new FilterRegistrationBean<>(filter);
        registration.setEnabled(false);
        return registration;
    }
}
