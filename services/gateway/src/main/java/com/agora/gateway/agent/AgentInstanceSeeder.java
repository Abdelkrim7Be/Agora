package com.agora.gateway.agent;

import com.agora.gateway.config.GatewayProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

@Component
public class AgentInstanceSeeder implements CommandLineRunner {

    private static final Logger log = LoggerFactory.getLogger(AgentInstanceSeeder.class);

    private final AgentInstanceRepository instances;
    private final GatewayProperties props;

    public AgentInstanceSeeder(AgentInstanceRepository instances, GatewayProperties props) {
        this.instances = instances;
        this.props = props;
    }

    @Override
    public void run(String... args) {
        String id = props.getDefaultAgentInstance();
        // Only seed on a genuinely empty table (fresh install) — checking
        // existsById(id) instead resurrected a deliberately deleted default
        // instance on every gateway restart, even with other instances present.
        if (id == null || id.isBlank() || instances.count() > 0) return;

        GatewayProperties.AgentType type = props.getAgentTypes().stream()
                .filter(t -> "email-agent".equals(t.getId()))
                .findFirst()
                .orElseGet(GatewayProperties.AgentType::defaultEmailAgent);

        // A literal "default-mailbox" shipped here before. It is not an address, and
        // the email-agent compares this value against the mailbox Google actually
        // authorized and fails closed on a mismatch — so every clean install seeded an
        // instance that could never connect Gmail. Blank restores the documented
        // "no expectation" path; a configured value pins the instance to one mailbox.
        String mailbox = props.getDefaultAgentMailbox();

        instances.save(new AgentInstance(
                id,
                type.getId(),
                "Default Email Agent",
                mailbox == null ? "" : mailbox.trim(),
                "Seeded email-agent instance that preserves the original single-mailbox flow.",
                "active",
                type.getBasePath(),
                "owner,viewer",
                "system",
                type.getColor(),
                type.getIcon()
        ));
        log.info("seeded agent instance {} ({})", id, type.getId());
    }
}
