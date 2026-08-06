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
import static com.github.tomakehurst.wiremock.client.WireMock.equalTo;
import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static org.hamcrest.Matchers.hasItem;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:agentregistrydb;DB_CLOSE_DELAY=-1",
        "spring.datasource.driver-class-name=org.h2.Driver",
        "spring.datasource.username=sa",
        "spring.datasource.password=",
        "spring.jpa.database-platform=org.hibernate.dialect.H2Dialect",
        "spring.jpa.hibernate.ddl-auto=create-drop",
        "gateway.upstream.summary-cache-seconds=0",
        "gateway.jwt.secret=test-secret-test-secret-test-secret-0123",
        "gateway.default-agent-instance=default-email-agent",
        "gateway.owner.username=owner",
        "gateway.owner.password=ownerpass",
        "gateway.viewer.username=viewer",
        "gateway.viewer.password=viewerpass",
        "gateway.admin.username=admin",
        "gateway.admin.password=adminpass"
})
class AgentRegistryTest {

    private static WireMockServer wireMock;

    @DynamicPropertySource
    static void wireMockProperties(DynamicPropertyRegistry registry) {
        wireMock = new WireMockServer(WireMockConfiguration.wireMockConfig().dynamicPort());
        wireMock.start();
        registry.add("gateway.upstream.email-agent-url",
                () -> "http://localhost:" + wireMock.port());
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

    @Test
    void agents_returns_configured_types_with_health() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/health"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"status\":\"ok\"}")));

        mockMvc.perform(get("/agents")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].id").value("email-agent"))
                .andExpect(jsonPath("$[0].display_name").value("Email Agent"))
                .andExpect(jsonPath("$[0].capabilities", hasItem("gmail_sync")))
                .andExpect(jsonPath("$[0].base_path").value("/api/agent"))
                .andExpect(jsonPath("$[0].health").value("healthy"));

        wireMock.verify(1, getRequestedFor(urlEqualTo("/health")));
    }

    @Test
    void agent_instances_returns_visible_seed_with_email_summary() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/health"))
                .willReturn(aResponse().withStatus(200)));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/drafts"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"drafts\":[{\"run_id\":\"run-1\"},{\"run_id\":\"run-2\"}]}")));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/costs/summary?period=day"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"totals\":{\"cost_eur\":1.25}}")));

        mockMvc.perform(get("/agent-instances")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].id").value("default-email-agent"))
                .andExpect(jsonPath("$[0].agent_type").value("email-agent"))
                .andExpect(jsonPath("$[0].display_name").value("Default Email Agent"))
                .andExpect(jsonPath("$[0].effective_role").value("viewer"))
                .andExpect(jsonPath("$[0].summary.service_health").value("healthy"))
                .andExpect(jsonPath("$[0].summary.pending_drafts").value(2))
                .andExpect(jsonPath("$[0].summary.today_cost_eur").value(1.25));

        wireMock.verify(getRequestedFor(urlEqualTo("/drafts"))
                .withHeader("X-Agora-User", equalTo("viewer"))
                .withHeader("X-Agora-Agent-Instance", equalTo("default-email-agent")));
    }

    @Test
    void admin_can_create_agent_instance() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/health"))
                .willReturn(aResponse().withStatus(200)));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/drafts"))
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody("{\"drafts\":[]}")));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/costs/summary?period=day"))
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody("{\"totals\":{\"cost_eur\":0.0}}")));

        String body = objectMapper.writeValueAsString(Map.of(
                "id", "ceo-email-agent",
                "agent_type", "email-agent",
                "display_name", "CEO Email Agent",
                "mailbox_identity", "ceo@example.com",
                "description", "CEO mailbox workspace"
        ));

        // Admin keeps platform-wide creation, but owner is also allowed to create
        // self-service email-agent instances under the per-user quota.
        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.id").value("ceo-email-agent"))
                .andExpect(jsonPath("$.created_by").value("owner"));

        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + login("admin", "adminpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "id", "admin-ceo-email-agent",
                                "agent_type", "email-agent",
                                "display_name", "Admin CEO Email Agent",
                                "mailbox_identity", "admin-ceo@example.com"
                        ))))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.id").value("admin-ceo-email-agent"))
                .andExpect(jsonPath("$.mailbox_identity").value("admin-ceo@example.com"))
                .andExpect(jsonPath("$.created_by").value("admin"));
    }

    @Test
    void admin_can_rename_agent_instance() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/health"))
                .willReturn(aResponse().withStatus(200)));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/drafts"))
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody("{\"drafts\":[]}")));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/costs/summary?period=day"))
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody("{\"totals\":{\"cost_eur\":0.0}}")));

        String token = login("admin", "adminpass");
        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "id", "rename-email-agent",
                                "agent_type", "email-agent",
                                "display_name", "Old Name"))))
                .andExpect(status().isCreated());

        mockMvc.perform(put("/agent-instances/rename-email-agent")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"display_name\":\"New Name\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.display_name").value("New Name"));

        mockMvc.perform(get("/agent-instances")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[?(@.id == \"rename-email-agent\")].display_name").value(hasItem("New Name")));
    }

    @Test
    void admin_can_delete_agent_instance() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/health"))
                .willReturn(aResponse().withStatus(200)));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/drafts"))
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody("{\"drafts\":[]}")));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/costs/summary?period=day"))
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody("{\"totals\":{\"cost_eur\":0.0}}")));

        String token = login("admin", "adminpass");
        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "id", "remove-email-agent",
                                "agent_type", "email-agent",
                                "display_name", "Remove Me"))))
                .andExpect(status().isCreated());

        mockMvc.perform(delete("/agent-instances/remove-email-agent")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isNoContent());

        mockMvc.perform(get("/agent-instances")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[?(@.id == \"remove-email-agent\")]").isEmpty());
    }

    @Test
    void deactivated_agent_instance_stays_visible_to_admin_for_reactivation() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/health"))
                .willReturn(aResponse().withStatus(200)));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/drafts"))
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody("{\"drafts\":[]}")));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/costs/summary?period=day"))
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody("{\"totals\":{\"cost_eur\":0.0}}")));

        String token = login("admin", "adminpass");
        String body = objectMapper.writeValueAsString(Map.of(
                "id", "delete-me-email-agent",
                "agent_type", "email-agent",
                "display_name", "Delete Me Email Agent",
                "mailbox_identity", "delete-me@example.com"
        ));
        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated());

        mockMvc.perform(post("/agent-instances/delete-me-email-agent/deactivate")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("inactive"));

        mockMvc.perform(get("/agent-instances")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[?(@.id == 'delete-me-email-agent')].status").value(org.hamcrest.Matchers.hasItem("inactive")));

        mockMvc.perform(get("/agent-instances")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[?(@.id == 'delete-me-email-agent')]").isEmpty());
    }

    @Test
    void viewer_can_create_email_agent_instance() throws Exception {
        String body = objectMapper.writeValueAsString(Map.of(
                "agent_type", "email-agent",
                "display_name", "HR Email Agent"
        ));

        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.agent_type").value("email-agent"))
                .andExpect(jsonPath("$.created_by").value("viewer"))
                .andExpect(jsonPath("$.effective_role").value("owner"));
    }

    @Test
    void viewer_self_service_email_agent_instances_are_limited_to_five() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/health"))
                .willReturn(aResponse().withStatus(200)));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/drafts"))
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody("{\"drafts\":[]}")));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/costs/summary?period=day"))
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody("{\"totals\":{\"cost_eur\":0.0}}")));

        String adminToken = login("admin", "adminpass");
        mockMvc.perform(post("/users")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "username", "quota_viewer",
                                "password", "quota-pass",
                                "role", "viewer"
                        ))))
                .andExpect(status().isCreated());

        String token = login("quota_viewer", "quota-pass");
        for (int i = 1; i <= 5; i += 1) {
            mockMvc.perform(post("/agent-instances")
                            .header("Authorization", "Bearer " + token)
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(objectMapper.writeValueAsString(Map.of(
                                    "id", "viewer-email-agent-" + i,
                                    "agent_type", "email-agent",
                                    "display_name", "Viewer Email Agent " + i
                            ))))
                    .andExpect(status().isCreated());
        }

        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "id", "viewer-email-agent-6",
                                "agent_type", "email-agent",
                                "display_name", "Viewer Email Agent 6"
                        ))))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("email-agent instance limit reached: 5"));
    }

    @Test
    void admin_can_create_and_deactivate_agent_instance() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/health"))
                .willReturn(aResponse().withStatus(200)));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/drafts"))
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody("{\"drafts\":[]}")));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/costs/summary?period=day"))
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody("{\"totals\":{\"cost_eur\":0.0}}")));

        String token = login("admin", "adminpass");
        String body = objectMapper.writeValueAsString(Map.of(
                "id", "admin-email-agent",
                "agent_type", "email-agent",
                "display_name", "Admin Email Agent",
                "mailbox_identity", "admin@example.com"
        ));

        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.id").value("admin-email-agent"));

        mockMvc.perform(post("/agent-instances/admin-email-agent/deactivate")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("inactive"));
    }

}
