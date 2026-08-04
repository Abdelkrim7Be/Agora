package com.agora.gateway.agent;

import com.fasterxml.jackson.annotation.JsonProperty;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * One row per mailbox the caller can reach, with its live connection state.
 *
 * Answers "are all my mailboxes healthy?" without visiting each workspace in
 * turn. The aggregation happens here rather than in the browser for three
 * reasons: the gateway is the only component that knows the grant table, so
 * authorization stays in one place; the browser makes one request instead of
 * one per instance; and a slow or unreachable agent degrades to an error on its
 * own row instead of a failed page.
 */
@RestController
public class MailboxOverviewController {

    private final AgentRegistryService service;
    private final InstanceGrantService grants;

    public MailboxOverviewController(AgentRegistryService service, InstanceGrantService grants) {
        this.service = service;
        this.grants = grants;
    }

    record MailboxResponse(
            String id,
            @JsonProperty("display_name") String displayName,
            @JsonProperty("mailbox_identity") String mailboxIdentity,
            @JsonProperty("agent_type") String agentType,
            String status,
            String color,
            String icon,
            @JsonProperty("effective_role") String effectiveRole,
            Map<String, Object> mailbox
    ) {}

    @GetMapping("/mailboxes")
    public List<MailboxResponse> mailboxes(Authentication auth) {
        String username = auth.getName();
        String role = role(auth);
        // One upstream round trip per instance; in parallel the page costs about
        // as much as the slowest single mailbox rather than the sum of them.
        return service.visibleInstances(username, role).parallelStream()
                .map(instance -> new MailboxResponse(
                        instance.getId(),
                        instance.getDisplayName(),
                        instance.getMailboxIdentity(),
                        instance.getAgentType(),
                        instance.getStatus(),
                        instance.getColor(),
                        instance.getIcon(),
                        grants.effectiveRole(instance.getId(), username, role).orElse(""),
                        service.mailboxStatus(instance, username)))
                .toList();
    }

    private String role(Authentication auth) {
        return auth.getAuthorities().stream()
                .map(GrantedAuthority::getAuthority)
                .map(a -> a.startsWith("ROLE_") ? a.substring(5).toLowerCase() : a)
                .findFirst()
                .orElse("");
    }
}
