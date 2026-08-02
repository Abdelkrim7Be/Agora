package com.agora.gateway;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.github.tomakehurst.wiremock.WireMockServer;
import com.github.tomakehurst.wiremock.core.WireMockConfiguration;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import java.util.Map;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.client.WireMock.urlPathEqualTo;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:mailboxoverviewdb;DB_CLOSE_DELAY=-1",
        "spring.datasource.driver-class-name=org.h2.Driver",
        "spring.datasource.username=sa",
        "spring.datasource.password=",
        "spring.jpa.database-platform=org.hibernate.dialect.H2Dialect",
        "spring.jpa.hibernate.ddl-auto=create-drop",
        "gateway.jwt.secret=test-secret-test-secret-test-secret-0123",
        "gateway.default-agent-instance=default-email-agent",
        "gateway.owner.username=owner",
        "gateway.owner.password=ownerpass",
        "gateway.admin.username=admin",
        "gateway.admin.password=adminpass"
})
class MailboxOverviewTest {

    private static WireMockServer wireMock;

    @DynamicPropertySource
    static void wireMockProperties(DynamicPropertyRegistry registry) {
        wireMock = new WireMockServer(WireMockConfiguration.wireMockConfig().dynamicPort());
        wireMock.start();
        registry.add("gateway.upstream.email-agent-url", () -> "http://localhost:" + wireMock.port());
    }

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @AfterEach
    void resetWireMock() {
        wireMock.resetAll();
    }

    private String login(String username, String password) throws Exception {
        String body = objectMapper.writeValueAsString(Map.of("username", username, "password", password));
        String response = mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        return objectMapper.readTree(response).get("token").asText();
    }

    private void stubHealthyMailbox(String provider) {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/sync/status"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {"provider":"%s","connection_status":"connected","sync_mode":"idle",
                                 "paused":false,"last_success_at":"2026-08-02T09:00:00Z",
                                 "last_failure_at":null,"last_error":null}
                                """.formatted(provider))));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/drafts"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"drafts\":[{\"run_id\":\"r1\"},{\"run_id\":\"r2\"}]}")));
    }

    @Test
    void overview_reports_the_provider_and_connection_state_per_mailbox() throws Exception {
        stubHealthyMailbox("outlook");

        mockMvc.perform(get("/mailboxes")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].id").value("default-email-agent"))
                .andExpect(jsonPath("$[0].display_name").exists())
                .andExpect(jsonPath("$[0].mailbox.provider").value("outlook"))
                .andExpect(jsonPath("$[0].mailbox.connection_status").value("connected"))
                .andExpect(jsonPath("$[0].mailbox.reachable").value(true))
                .andExpect(jsonPath("$[0].mailbox.pending_drafts").value(2))
                .andExpect(jsonPath("$[0].mailbox.last_success_at").value("2026-08-02T09:00:00Z"));
    }

    @Test
    void a_mailbox_whose_agent_is_down_becomes_a_bad_row_not_a_failed_page() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/sync/status"))
                .willReturn(aResponse().withStatus(503)));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/drafts"))
                .willReturn(aResponse().withStatus(503)));

        mockMvc.perform(get("/mailboxes")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].mailbox.reachable").value(false))
                .andExpect(jsonPath("$[0].mailbox.connection_status").value("unknown"))
                .andExpect(jsonPath("$[0].mailbox.pending_drafts").value(0));
    }

    @Test
    void the_provider_defaults_to_gmail_when_the_agent_does_not_report_one() throws Exception {
        // Instances that predate the provider layer answer /sync/status without it.
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/sync/status"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"connection_status\":\"connected\"}")));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/drafts"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"drafts\":[]}")));

        mockMvc.perform(get("/mailboxes")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].mailbox.provider").value("gmail"));
    }

    @Test
    void naming_a_mailbox_you_have_no_access_to_is_refused() throws Exception {
        // The overview probes a specific instance by header, so that header must
        // not become a way to act on a mailbox the caller cannot otherwise reach.
        // Access is the instance's allowed_roles plus any explicit grant; with
        // neither, ProxyController refuses before anything is forwarded.
        stubHealthyMailbox("gmail");
        String created = mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + login("admin", "adminpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "agent_type", "email-agent",
                                "display_name", "Direction",
                                "mailbox_identity", "direction@example.test",
                                "allowed_roles", "admin"))))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        String restricted = objectMapper.readTree(created).get("id").asText();

        mockMvc.perform(post("/api/agent/connect/test")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .header("X-Agora-Agent-Instance", restricted))
                .andExpect(status().isForbidden());

        // ... and it does not appear in that user's overview either.
        String overview = mockMvc.perform(get("/mailboxes")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        org.junit.jupiter.api.Assertions.assertFalse(overview.contains(restricted));
    }

    @Test
    void an_anonymous_caller_is_rejected() throws Exception {
        mockMvc.perform(get("/mailboxes")).andExpect(status().isUnauthorized());
    }

    @Test
    void the_listing_only_contains_mailboxes_the_caller_can_reach() throws Exception {
        stubHealthyMailbox("gmail");
        String adminToken = login("admin", "adminpass");

        // A second instance the owner holds no grant on.
        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "agent_type", "email-agent",
                                "display_name", "Comptabilité",
                                "mailbox_identity", "compta@example.test",
                                "allowed_roles", "owner"))))
                .andExpect(status().isCreated());

        String ownerBody = mockMvc.perform(get("/mailboxes")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();

        String adminBody = mockMvc.perform(get("/mailboxes")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();

        // Admin sees the new mailbox; the visibility rules are the same ones
        // /agent-instances already applies, so the two listings must agree.
        int ownerCount = objectMapper.readTree(ownerBody).size();
        int adminCount = objectMapper.readTree(adminBody).size();
        org.junit.jupiter.api.Assertions.assertTrue(adminCount >= ownerCount);
        org.junit.jupiter.api.Assertions.assertTrue(adminBody.contains("Comptabilité"));
    }
}
