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
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
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
        "gateway.jwt.secret=test-secret-test-secret-test-secret-0123",
        "gateway.default-agent-instance=default-email-agent",
        "gateway.owner.username=owner",
        "gateway.owner.password=ownerpass",
        "gateway.viewer.username=viewer",
        "gateway.viewer.password=viewerpass"
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
    void owner_can_create_agent_instance() throws Exception {
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

        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.id").value("ceo-email-agent"))
                .andExpect(jsonPath("$.mailbox_identity").value("ceo@example.com"))
                .andExpect(jsonPath("$.created_by").value("owner"));
    }

    @Test
    void deleted_agent_instance_is_hidden_from_directory() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/health"))
                .willReturn(aResponse().withStatus(200)));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/drafts"))
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody("{\"drafts\":[]}")));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/costs/summary?period=day"))
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody("{\"totals\":{\"cost_eur\":0.0}}")));

        String token = login("owner", "ownerpass");
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

        mockMvc.perform(delete("/agent-instances/delete-me-email-agent")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("inactive"));

        mockMvc.perform(get("/agent-instances")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[?(@.id == 'delete-me-email-agent')]").isEmpty());
    }

    @Test
    void viewer_cannot_create_agent_instance() throws Exception {
        String body = objectMapper.writeValueAsString(Map.of(
                "agent_type", "email-agent",
                "display_name", "HR Email Agent"
        ));

        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isForbidden());
    }
}
