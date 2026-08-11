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
import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
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
        "gateway.viewer.password=viewerpass",
        "gateway.admin.username=admin",
        "gateway.admin.password=adminpass"
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

    private void createOwnerInstance(String instanceId) throws Exception {
        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "id", instanceId,
                                "agent_type", "email-agent",
                                "display_name", instanceId
                        ))))
                .andExpect(status().isCreated());
    }

    private void grantViewerOn(String instanceId, String role) throws Exception {
        mockMvc.perform(post("/agent-instances/" + instanceId + "/grants")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "user_id", "viewer",
                                "role", role
                        ))))
                .andExpect(status().isCreated());
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
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.expires_at").exists());

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
    void viewer_grant_on_private_instance_allows_read_but_not_approve_or_write() throws Exception {
        createOwnerInstance("private-viewer-boundary");
        grantViewerOn("private-viewer-boundary", "viewer");

        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/inbox"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"messages\":[]}")));

        String viewerToken = login("viewer", "viewerpass");
        mockMvc.perform(get("/api/agent/inbox")
                        .header("Authorization", "Bearer " + viewerToken)
                        .header("X-Agora-Agent-Instance", "private-viewer-boundary"))
                .andExpect(status().isOk());

        wireMock.verify(1, getRequestedFor(urlPathEqualTo("/inbox"))
                .withHeader("X-Agora-User", equalTo("viewer"))
                .withHeader("X-Agora-Agent-Instance", equalTo("private-viewer-boundary"))
                .withHeader("X-Agora-Instance-Role", equalTo("viewer")));

        mockMvc.perform(post("/api/agent/run/xyz/approve")
                        .header("Authorization", "Bearer " + viewerToken)
                        .header("X-Agora-Agent-Instance", "private-viewer-boundary")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isForbidden());

        mockMvc.perform(post("/api/agent/categories")
                        .header("Authorization", "Bearer " + viewerToken)
                        .header("X-Agora-Agent-Instance", "private-viewer-boundary")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isForbidden());

        wireMock.verify(0, postRequestedFor(urlPathEqualTo("/run/xyz/approve")));
        wireMock.verify(0, postRequestedFor(urlPathEqualTo("/categories")));
    }

    @Test
    void approver_grant_on_private_instance_allows_read_and_approve_but_not_write() throws Exception {
        createOwnerInstance("private-approver-boundary");
        grantViewerOn("private-approver-boundary", "approver");

        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/inbox"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"messages\":[]}")));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/run/xyz/approve"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"run_id\":\"xyz\",\"status\":\"completed\"}")));

        String viewerToken = login("viewer", "viewerpass");
        mockMvc.perform(get("/api/agent/inbox")
                        .header("Authorization", "Bearer " + viewerToken)
                        .header("X-Agora-Agent-Instance", "private-approver-boundary"))
                .andExpect(status().isOk());

        mockMvc.perform(post("/api/agent/run/xyz/approve")
                        .header("Authorization", "Bearer " + viewerToken)
                        .header("X-Agora-Agent-Instance", "private-approver-boundary")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isOk());

        wireMock.verify(1, getRequestedFor(urlPathEqualTo("/inbox"))
                .withHeader("X-Agora-User", equalTo("viewer"))
                .withHeader("X-Agora-Agent-Instance", equalTo("private-approver-boundary"))
                .withHeader("X-Agora-Instance-Role", equalTo("approver")));
        wireMock.verify(1, postRequestedFor(urlPathEqualTo("/run/xyz/approve"))
                .withHeader("X-Agora-User", equalTo("viewer"))
                .withHeader("X-Agora-Agent-Instance", equalTo("private-approver-boundary"))
                .withHeader("X-Agora-Instance-Role", equalTo("approver")));

        mockMvc.perform(post("/api/agent/categories")
                        .header("Authorization", "Bearer " + viewerToken)
                        .header("X-Agora-Agent-Instance", "private-approver-boundary")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isForbidden());

        wireMock.verify(0, postRequestedFor(urlPathEqualTo("/categories")));
    }

    @Test
    void expired_viewer_grant_does_not_allow_proxy_read() throws Exception {
        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "id", "private-expiring",
                                "agent_type", "email-agent",
                                "display_name", "Private Expiring"
                        ))))
                .andExpect(status().isCreated());

        String grantBody = objectMapper.writeValueAsString(Map.of(
                "user_id", "viewer",
                "role", "viewer",
                "expires_at", "2020-01-01T00:00:00Z"
        ));
        mockMvc.perform(post("/agent-instances/private-expiring/grants")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(grantBody))
                .andExpect(status().isCreated());

        mockMvc.perform(get("/api/agent/inbox")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .header("X-Agora-Agent-Instance", "private-expiring"))
                .andExpect(status().isForbidden());

        wireMock.verify(0, getRequestedFor(urlPathEqualTo("/inbox")));
    }

    @Test
    void deactivated_instance_is_denied_at_proxy() throws Exception {
        String adminToken = login("admin", "adminpass");

        // Create an instance, then deactivate it.
        String createBody = objectMapper.writeValueAsString(Map.of(
                "id", "temp-deactivated", "agent_type", "email-agent", "display_name", "Temp"));
        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(createBody))
                .andExpect(status().isCreated());
        mockMvc.perform(post("/agent-instances/temp-deactivated/deactivate")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk());

        // Even an admin cannot proxy to a deactivated instance.
        mockMvc.perform(get("/api/agent/run/xyz")
                        .header("Authorization", "Bearer " + adminToken)
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

    @Test
    void admin_without_instance_owner_role_cannot_add_grant() throws Exception {
        String body = objectMapper.writeValueAsString(Map.of("user_id", "admin-added", "role", "viewer"));
        mockMvc.perform(post("/agent-instances/default-email-agent/grants")
                        .header("Authorization", "Bearer " + login("admin", "adminpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isForbidden());
    }
}
