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
        if (id == null || id.isBlank() || instances.existsById(id)) return;

        GatewayProperties.AgentType type = props.getAgentTypes().stream()
                .filter(t -> "email-agent".equals(t.getId()))
                .findFirst()
                .orElseGet(GatewayProperties.AgentType::defaultEmailAgent);

        instances.save(new AgentInstance(
                id,
                type.getId(),
                "Default Email Agent",
                "default-mailbox",
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
