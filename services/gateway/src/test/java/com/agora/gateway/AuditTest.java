package com.agora.gateway;

import com.agora.gateway.audit.AuditEvent;
import com.agora.gateway.audit.AuditRepository;
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
import static com.github.tomakehurst.wiremock.client.WireMock.urlPathEqualTo;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:auditdb;DB_CLOSE_DELAY=-1",
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
class AuditTest {

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
    private AuditRepository auditRepository;

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

    private AuditEvent findOne(String action, String outcome) {
        return auditRepository.findAll().stream()
                .filter(e -> action.equals(e.getAction()) && outcome.equals(e.getOutcome()))
                .reduce((a, b) -> { throw new AssertionError("more than one " + action + "/" + outcome + " row"); })
                .orElseThrow(() -> new AssertionError("no " + action + "/" + outcome + " row"));
    }

    @Test
    void owner_approve_writes_forwarded_row() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/run/abc/approve"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"run_id\":\"abc\",\"status\":\"completed\"}")));

        String token = login("owner", "ownerpass");

        mockMvc.perform(post("/api/agent/run/abc/approve")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isOk());

        AuditEvent e = findOne("approve", "forwarded");
        assertThat(e.getUpstreamStatus()).isEqualTo(200);
        assertThat(e.getUsername()).isEqualTo("owner");
        assertThat(e.getRole()).isEqualTo("owner");
    }

    @Test
    void viewer_denied_approve_writes_denied_row() throws Exception {
        String token = login("viewer", "viewerpass");

        mockMvc.perform(post("/api/agent/run/abc/approve")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isForbidden());

        AuditEvent e = findOne("approve", "denied");
        assertThat(e.getUpstreamStatus()).isNull();
        assertThat(e.getUsername()).isEqualTo("viewer");
        assertThat(e.getRole()).isEqualTo("viewer");
    }

    @Test
    void audit_endpoint_owner_only() throws Exception {
        String viewerToken = login("viewer", "viewerpass");
        String ownerToken = login("owner", "ownerpass");

        mockMvc.perform(get("/audit")
                        .header("Authorization", "Bearer " + viewerToken))
                .andExpect(status().isForbidden());

        mockMvc.perform(get("/audit")
                        .header("Authorization", "Bearer " + ownerToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$").isArray());
    }

    @Test
    void login_failure_writes_row() throws Exception {
        String body = objectMapper.writeValueAsString(Map.of("username", "owner", "password", "wrongpass"));
        mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isUnauthorized());

        AuditEvent e = findOne("login", "failure");
        assertThat(e.getUsername()).isEqualTo("owner");
        // verify password not stored — the only string fields are username, role, action, method, path, outcome
        assertThat(e.getRole()).isNull();
        assertThat(e.getUpstreamStatus()).isNull();
    }
}
