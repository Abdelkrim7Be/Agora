package com.agora.gateway;

import com.agora.gateway.agent.AgentInstanceGrantRepository;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.github.tomakehurst.wiremock.WireMockServer;
import com.github.tomakehurst.wiremock.core.WireMockConfiguration;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
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
import static com.github.tomakehurst.wiremock.client.WireMock.postRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlPathEqualTo;
import static org.hamcrest.Matchers.hasSize;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:grantdb;DB_CLOSE_DELAY=-1",
        "spring.datasource.driver-class-name=org.h2.Driver",
        "spring.datasource.username=sa",
        "spring.datasource.password=",
        "spring.jpa.database-platform=org.hibernate.dialect.H2Dialect",
        "spring.jpa.hibernate.ddl-auto=create-drop",
        "gateway.jwt.secret=test-secret-test-secret-test-secret-0123",
        "gateway.owner.username=owner",
        "gateway.owner.password=ownerpass",
        "gateway.viewer.username=viewer",
        "gateway.viewer.password=viewerpass"
})
class InstanceGrantTest {

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

    @Autowired
    private AgentInstanceGrantRepository grantRepository;

    @BeforeEach
    void cleanupGrants() {
        grantRepository.findByAgentInstanceId("default-email-agent")
                .forEach(g -> grantRepository.deleteById(g.getId()));
    }

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
    void owner_can_list_grants_empty() throws Exception {
        mockMvc.perform(get("/agent-instances/default-email-agent/grants")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$").isArray());
    }

    @Test
    void owner_can_add_grant_and_list_it() throws Exception {
        String body = objectMapper.writeValueAsString(Map.of("user_id", "alice", "role", "approver"));
        mockMvc.perform(post("/agent-instances/default-email-agent/grants")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.role").value("approver"))
                .andExpect(jsonPath("$.user_id").value("alice"));

        mockMvc.perform(get("/agent-instances/default-email-agent/grants")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[?(@.user_id == 'alice')]").exists());
    }

    @Test
    void owner_can_delete_grant() throws Exception {
        String body = objectMapper.writeValueAsString(Map.of("user_id", "bob", "role", "viewer"));
        mockMvc.perform(post("/agent-instances/default-email-agent/grants")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated());

        mockMvc.perform(delete("/agent-instances/default-email-agent/grants/bob")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isNoContent());
    }

    @Test
    void viewer_cannot_add_grant_403() throws Exception {
        String body = objectMapper.writeValueAsString(Map.of("user_id", "eve", "role", "viewer"));
        mockMvc.perform(post("/agent-instances/default-email-agent/grants")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isForbidden());
    }

    @Test
    void viewer_cannot_list_grants_403() throws Exception {
        mockMvc.perform(get("/agent-instances/default-email-agent/grants")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isForbidden());
    }

    @Test
    void invalid_role_returns_400() throws Exception {
        String body = objectMapper.writeValueAsString(Map.of("user_id", "eve", "role", "superadmin"));
        mockMvc.perform(post("/agent-instances/default-email-agent/grants")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isBadRequest());
    }

    @Test
    void approver_grant_allows_approve_on_instance() throws Exception {
        // Grant viewer-JWT user "viewer" an approver role on the default instance.
        String grantBody = objectMapper.writeValueAsString(Map.of("user_id", "viewer", "role", "approver"));
        mockMvc.perform(post("/agent-instances/default-email-agent/grants")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(grantBody))
                .andExpect(status().isCreated());

        // Stub the upstream approve endpoint.
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/run/xyz/approve"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"run_id\":\"xyz\",\"status\":\"completed\"}")));

        // viewer JWT + approver grant → should reach upstream.
        mockMvc.perform(post("/api/agent/run/xyz/approve")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isOk());

        wireMock.verify(1, postRequestedFor(urlPathEqualTo("/run/xyz/approve"))
                .withHeader("X-Agora-Instance-Role", equalTo("approver")));
    }

    @Test
    void deactivated_instance_is_denied_at_proxy() throws Exception {
        String ownerToken = login("owner", "ownerpass");

        // Create an instance, then deactivate it.
        String createBody = objectMapper.writeValueAsString(Map.of(
                "id", "temp-deactivated", "agent_type", "email-agent", "display_name", "Temp"));
        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + ownerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(createBody))
                .andExpect(status().isCreated());
        mockMvc.perform(delete("/agent-instances/temp-deactivated")
                        .header("Authorization", "Bearer " + ownerToken))
                .andExpect(status().isOk());

        // Even an owner cannot proxy to a deactivated instance.
        mockMvc.perform(get("/api/agent/run/xyz")
                        .header("Authorization", "Bearer " + ownerToken)
                        .header("X-Agora-Agent-Instance", "temp-deactivated"))
                .andExpect(status().isForbidden());
    }

    @Test
    void viewer_without_grant_denied_on_approve() throws Exception {
        // No grant for viewer on default instance beyond JWT viewer role.
        // ProxyController should deny because viewer tier < approver.
        mockMvc.perform(post("/api/agent/run/xyz/approve")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isForbidden());
    }
}
