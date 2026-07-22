package com.agora.gateway.agent;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.Statement;

/** Grants the email-agent runtime role read-only registry discovery access. */
@Component
public class AgentRegistryDatabaseGrant implements CommandLineRunner {

    private static final Logger log = LoggerFactory.getLogger(AgentRegistryDatabaseGrant.class);
    private static final String EMAIL_AGENT_ROLE = "agora_email_agent";

    private final DataSource dataSource;

    public AgentRegistryDatabaseGrant(DataSource dataSource) {
        this.dataSource = dataSource;
    }

    @Override
    public void run(String... args) throws Exception {
        try (Connection connection = dataSource.getConnection()) {
            if (!"PostgreSQL".equalsIgnoreCase(connection.getMetaData().getDatabaseProductName())) {
                return;
            }
            try (Statement statement = connection.createStatement()) {
                statement.execute("""
                        DO $$
                        BEGIN
                            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agora_email_agent') THEN
                                GRANT SELECT ON agent_instance TO agora_email_agent;
                            END IF;
                        END
                        $$
                        """);
            }
        }
        log.info("granted {} read-only agent registry access", EMAIL_AGENT_ROLE);
    }
}
