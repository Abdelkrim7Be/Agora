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
                .requestMatchers(HttpMethod.GET, "/actuator/health").permitAll()
                .requestMatchers(HttpMethod.GET, "/actuator/prometheus").permitAll()
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
                // The invitee has no account yet, so there is no JWT to present:
                // the signed single-use token in the path is the credential.
                // Both legs are throttled globally in InvitationController.
                .requestMatchers(HttpMethod.GET, "/auth/invite/*").permitAll()
                .requestMatchers(HttpMethod.POST, "/auth/invite/*").permitAll()
                // Someone who forgot their password has no token to present. The
                // endpoint answers identically whatever the address, and never
                // returns the link — see PasswordResetController.
                .requestMatchers(HttpMethod.POST, "/auth/forgot-password").permitAll()
                // Every signed-in account may read and edit its own profile and rotate
                // its own password. The account is taken from the token, never from
                // the request, so there is no id to swap for someone else's.
                .requestMatchers(HttpMethod.GET, "/me").authenticated()
                .requestMatchers(HttpMethod.PUT, "/me").authenticated()
                .requestMatchers(HttpMethod.PUT, "/me/password").authenticated()
                .requestMatchers(HttpMethod.PUT, "/users/*/password").hasRole("ADMIN")
                // Recovery path for an account whose second-factor device is gone.
                .requestMatchers(HttpMethod.POST, "/users/*/mfa/reset").hasRole("ADMIN")
                .requestMatchers(HttpMethod.GET, "/users").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/users").hasRole("ADMIN")
                .requestMatchers(HttpMethod.PUT, "/users/*").hasRole("ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/users/*").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/users/*/disable").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/users/*/enable").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/users/*/anonymize").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/users/*/invite").hasRole("ADMIN")
                // Any signed-in role may file a report; only an admin sees the inbox.
                .requestMatchers(HttpMethod.POST, "/reports").authenticated()
                .requestMatchers(HttpMethod.GET, "/reports").hasRole("ADMIN")
                .requestMatchers(HttpMethod.PUT, "/reports/*").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/audit/run").hasRole("ADMIN")
                .requestMatchers(HttpMethod.GET, "/audit/runs").hasRole("ADMIN")
                .requestMatchers(HttpMethod.GET, "/audit/runs/*").hasRole("ADMIN")
                .requestMatchers(HttpMethod.GET, "/agents").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/mailboxes").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/agent-instances").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/agent-instances").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/agent-instances/*/admin-access").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/agent-instances/*").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/agent-instances/*/activate").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/agent-instances/*/deactivate").hasRole("ADMIN")
                // VIEWER/APPROVER stay in the coarse gate for the same reason as the
                // /api/agent/** write-tier routes: InstanceGrantController's own
                // canManageGrants() already requires effectiveRole == owner per
                // instance before anything runs, independent of platform role.
                .requestMatchers(HttpMethod.GET, "/agent-instances/*/grants").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/agent-instances/*/grants").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/agent-instances/*/grants/*").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/agent-instances/*").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/disconnect/gmail").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/style").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/memory").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/disconnect/outlook").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/webhooks/gmail").permitAll()
                .requestMatchers(HttpMethod.GET, "/api/agent/connect/gmail/callback").permitAll()
                // The provider redirects the browser here with no Bearer token; the
                // signed OAuth state is what authenticates the callback, exactly as
                // for Gmail.
                .requestMatchers(HttpMethod.GET, "/api/agent/connect/outlook/callback").permitAll()
                .requestMatchers(HttpMethod.GET, "/api/agent/agent-instances/*/connect/gmail/start").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/agent-instances/*/connect/outlook/start").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/connect/test").hasAnyRole("OWNER", "APPROVER", "ADMIN")
                .requestMatchers("/api/agent/campaigns/**").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/run").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/run/stream").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                // approve/reject/respond: VIEWER stays in the gate so a user whose global JWT
                // role is viewer but holds a per-instance approver grant can still reach
                // ProxyController, which enforces the real (instance-level) authorization.
                // APPROVER is now a first-class JWT role for users who are globally
                // approvers, not just grant-delegated.
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/approve").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/reject").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/respond").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/respond/stream").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                // Staging a file to attach at approval — same reviewer surface as
                // approve/reject/respond, checked again per-instance by ProxyController.
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/attachments").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                // Read-only AI helpers (thread summary, tone-adjust preview) — same access
                // as the rest of the approval surface; neither mutates run state.
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/summarize").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/run/*/tone").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/sync").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/sync/status").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/sync/pause").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/sync/resume").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                // Billing-sensitive: platform OWNER/VIEWER stay excluded even when they own
                // the instance — cost visibility is admin-tier by design, widened only far
                // enough to let a platform APPROVER with a real per-instance grant through
                // (ProxyController's instance-role check is the actual gate beyond this).
                .requestMatchers(HttpMethod.GET, "/api/agent/costs").hasAnyRole("APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/costs/**").hasAnyRole("APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/style").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/style").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/style/**").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/runs").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/events").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/runs/bulk").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/inbox").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/archive").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/trash").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/read").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/unread").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                // Re-queueing a message for the agent re-runs the pipeline on it, so it
                // sits with the other mailbox mutations rather than the read surface.
                // VIEWER/APPROVER stay in the coarse gate for the same reason as
                // approve/reject/respond above: a platform-viewer who holds a
                // per-instance owner grant must reach ProxyController, whose "write"
                // tier already requires effectiveRole == owner before anything runs.
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/force-agent").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/signature").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/signature").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/signature/image").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/signature/image").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                // Same authority as uploading one: it is the same act, with the
                // server doing the fetching. Without a rule here `anyRequest()
                // .denyAll()` answers 403 and the feature looks broken rather
                // than forbidden.
                .requestMatchers(HttpMethod.POST, "/api/agent/signature/image/from-url").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/signature/image").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/signature/apply").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/memory").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/memory").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/memory/summary").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/memory/item").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/config").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/config").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/send-mode").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/send-mode").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/persona").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/persona").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/persona/suggest").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/capabilities").hasRole("ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/capabilities").hasRole("ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/policy").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                // Distinct from the top-level /health (gateway's own liveness, permitAll,
                // used by the Docker healthcheck unauthenticated). This is the proxied
                // email-agent System Health aggregate for the browser control panel.
                .requestMatchers(HttpMethod.GET, "/api/agent/health").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                // Self-description only (capabilities, settings schema, contract
                // version) — no tenant data. AgentRegistryService already reads this
                // server-to-server; this is the same route for a browser/external
                // client asking the same question through the proxy.
                .requestMatchers(HttpMethod.GET, "/api/agent/manifest").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/metrics").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/dlq").hasRole("ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/dlq/*/requeue").hasRole("ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/roles").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/roles").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/roles/*").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/roles/*").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                // Category proposals from the mailbox scan: readable alongside the
                // categories themselves, but only an owner turns one into a category.
                // Declared before the generic /categories/* rules so the literal
                // "proposals" path never falls through to them.
                .requestMatchers(HttpMethod.GET, "/api/agent/categories/proposals").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/categories/proposals/accept").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/categories/proposals/dismiss").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/categories").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/categories").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/categories/*").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/categories/*").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/categories/test-match").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/categories/*/duplicate").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/templates").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/contacts").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/contacts").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/contacts/*").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/contacts/*").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/contacts/import").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/contacts/categorize").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/contacts/migrate-legacy").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/contacts/*/photo").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/contacts/*/photo").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/contacts/*/photo").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/segments").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/segments").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/segments/*").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/segments/*").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/drafts").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                // Junk-gate settings: readable by anyone who can see the workspace,
                // editable by the instance owner, same shape as the rules surface.
                // Block candidates derived from the mailbox: readable with the settings.
                .requestMatchers(HttpMethod.GET, "/api/agent/junk/suggestions").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/junk").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/junk").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/sensitivity").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/sensitivity").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/rules").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/rules").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/rules/suggestions").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                // Starter rules offered at onboarding; only an owner accepts one.
                .requestMatchers(HttpMethod.GET, "/api/agent/rules/starter").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/rules/starter/apply").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/rules/suggestions/*/promote").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/rules/rule-toggle").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/rules/section-toggle").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/rules/rule").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/rules/rule-delete").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/rules/section-config").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/rules/suggestions/*").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                // Poll cadence / batch caps. The endpoints existed on both sides but were
                // never enumerated here, so default-deny answered 403 for every role and
                // the workspace rendered a "reserved for another role" banner to everyone.
                .requestMatchers(HttpMethod.GET, "/api/agent/runtime-settings").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/runtime-settings").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/analytics").hasAnyRole("OWNER", "VIEWER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/alerts/settings").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/alerts/settings").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/retention/settings").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.PUT, "/api/agent/retention/settings").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/retention/dry-run").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/retention/run").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                // Right-to-erasure, same reach as retention: irreversible, so the
                // dry-run is listed alongside it rather than as a read-only route.
                .requestMatchers(HttpMethod.POST, "/api/agent/gdpr/erase/dry-run").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/gdpr/erase").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/claim").hasAnyRole("OWNER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/inbox/*/assign").hasAnyRole("OWNER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/run/**").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/instance-setup").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/instance-setup/start").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/instance-setup/retry").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/instance-setup/steps/*/retry").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/instance-setup/skip").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/notifications").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/agent/notifications/unread-count").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/notifications/read-all").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.POST, "/api/agent/notifications/*/read").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
                .requestMatchers(HttpMethod.DELETE, "/api/agent/notifications/*").hasAnyRole("OWNER", "VIEWER", "APPROVER", "ADMIN")
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
    // gateway. Origins are env-driven (GATEWAY_CORS_ALLOWED_ORIGINS, comma-separated) —
    // no wildcard default: refresh tokens use an HttpOnly cookie, so credentialed CORS
    // is enabled, and a credentialed wildcard origin is a real vulnerability, not a dev
    // convenience. The operator must set an explicit allowlist; compose always does.
    @Bean
    public CorsConfigurationSource corsConfigurationSource(
            @Value("${GATEWAY_CORS_ALLOWED_ORIGINS}") String allowedOrigins) {
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
