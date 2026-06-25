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
import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.postRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.client.WireMock.urlPathEqualTo;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:rbacdb;DB_CLOSE_DELAY=-1",
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
class RbacTest {

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
    void viewer_can_read_run() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/run/abc"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"run_id\":\"abc\",\"status\":\"pending_approval\"}")));

        mockMvc.perform(get("/api/agent/run/abc")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.run_id").value("abc"));

        wireMock.verify(1, getRequestedFor(urlPathEqualTo("/run/abc")));
    }

    @Test
    void viewer_cannot_approve_403() throws Exception {
        mockMvc.perform(post("/api/agent/run/abc/approve")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlEqualTo("/run/abc/approve")));
    }

    @Test
    void owner_can_approve() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/run/abc/approve"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"run_id\":\"abc\",\"status\":\"completed\"}")));

        mockMvc.perform(post("/api/agent/run/abc/approve")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.run_id").value("abc"));

        wireMock.verify(1, postRequestedFor(urlPathEqualTo("/run/abc/approve")));
    }

    @Test
    void viewer_cannot_trigger_403() throws Exception {
        mockMvc.perform(post("/api/agent/run")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlEqualTo("/run")));
    }

    @Test
    void viewer_cannot_sync_403() throws Exception {
        mockMvc.perform(post("/api/agent/sync?limit=7")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlEqualTo("/sync?limit=7")));
    }

    @Test
    void owner_can_read_cost_summary() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/costs/summary"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"period\":\"session\"}")));

        mockMvc.perform(get("/api/agent/costs/summary?period=session")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.period").value("session"));

        wireMock.verify(1, getRequestedFor(urlEqualTo("/costs/summary?period=session")));
    }

    @Test
    void viewer_cannot_read_cost_summary_403() throws Exception {
        mockMvc.perform(get("/api/agent/costs/summary?period=session")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, getRequestedFor(urlEqualTo("/costs/summary?period=session")));
    }



    @Test
    void owner_can_learn_style() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/style/learn"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"profile\":{}}")));

        mockMvc.perform(post("/api/agent/style/learn")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.profile").exists());

        wireMock.verify(1, postRequestedFor(urlEqualTo("/style/learn")));
    }

    @Test
    void viewer_cannot_learn_style_403() throws Exception {
        mockMvc.perform(post("/api/agent/style/learn")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlEqualTo("/style/learn")));
    }

    @Test
    void owner_denied_on_unenumerated_route() throws Exception {
        // default-deny: even an owner cannot reach an agent route that isn't explicitly allowed
        mockMvc.perform(post("/api/agent/run/abc/unknownverb")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlEqualTo("/run/abc/unknownverb")));
    }
}
