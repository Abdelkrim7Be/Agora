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

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.Statement;
import java.util.List;
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
        "gateway.viewer.password=viewerpass",
        "gateway.admin.username=admin",
        "gateway.admin.password=adminpass"
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

    @Autowired
    private DataSource dataSource;

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
    void audit_endpoint_admin_only() throws Exception {
        String viewerToken = login("viewer", "viewerpass");
        String ownerToken = login("owner", "ownerpass");
        String adminToken = login("admin", "adminpass");

        mockMvc.perform(get("/audit")
                        .header("Authorization", "Bearer " + viewerToken))
                .andExpect(status().isForbidden());

        mockMvc.perform(get("/audit")
                        .header("Authorization", "Bearer " + ownerToken))
                .andExpect(status().isForbidden());

        mockMvc.perform(get("/audit")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$").isArray());
    }

    @Test
    void routine_run_polling_is_not_written_to_audit() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/runs"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"runs\":[],\"has_more\":false}")));

        String token = login("owner", "ownerpass");

        mockMvc.perform(get("/api/agent/runs?status=pending_approval")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isOk());

        assertThat(auditRepository.findAll()).noneMatch(e ->
                "forwarded".equals(e.getOutcome()) && "/api/agent/runs".equals(e.getPath()));
    }

    @Test
    void polled_status_endpoints_are_not_written_to_audit() throws Exception {
        // These are what the open tabs hit on a timer. A row each pushed real
        // actions off the first page of the trail in about two minutes.
        String[] polled = {
                "/health", "/analytics", "/sync/status",
                "/instance-setup", "/notifications/unread-count",
        };
        for (String path : polled) {
            wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo(path))
                    .willReturn(aResponse()
                            .withStatus(200)
                            .withHeader("Content-Type", "application/json")
                            .withBody("{}")));
        }

        String token = login("owner", "ownerpass");
        for (String path : polled) {
            mockMvc.perform(get("/api/agent" + path)
                            .header("Authorization", "Bearer " + token))
                    .andExpect(status().isOk());
        }

        assertThat(auditRepository.findAll()).noneMatch(e -> "forwarded".equals(e.getOutcome()));
    }

    @Test
    void reading_one_run_is_still_written_to_audit() throws Exception {
        // The counterpart: anything that discloses a record must survive the
        // polling exclusion, or quieting the trail would also blind it.
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/run/run-42"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"run_id\":\"run-42\"}")));

        mockMvc.perform(get("/api/agent/run/run-42")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isOk());

        assertThat(auditRepository.findAll()).anyMatch(e ->
                "forwarded".equals(e.getOutcome()) && "/api/agent/run/run-42".equals(e.getPath()));
    }

    @Test
    void reading_the_inbox_is_still_written_to_audit() throws Exception {
        // The exclusion matches whole paths, not prefixes. /inbox lists real
        // senders, subjects and snippets — the disclosure the trail is for.
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/inbox"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"messages\":[]}")));

        mockMvc.perform(get("/api/agent/inbox")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isOk());

        assertThat(auditRepository.findAll()).anyMatch(e ->
                "forwarded".equals(e.getOutcome()) && "/api/agent/inbox".equals(e.getPath()));
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

    private void failedLogin(String username) throws Exception {
        // A distinct never-registered username per call — keeps each attempt in its
        // own per-username rate-limit bucket instead of sharing "owner"'s across tests.
        String body = objectMapper.writeValueAsString(Map.of("username", username, "password", "wrongpass"));
        mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void consecutive_rows_link_by_hash_and_verify_reports_valid() throws Exception {
        // Two distinct audit-generating actions guarantee at least two linked rows.
        failedLogin("no-such-user-chain-1");
        failedLogin("no-such-user-chain-2");
        String adminToken = login("admin", "adminpass");

        List<AuditEvent> rows = auditRepository.findAllByOrderByIdAsc();
        assertThat(rows.size()).isGreaterThanOrEqualTo(2);
        for (int i = 1; i < rows.size(); i++) {
            assertThat(rows.get(i).getPrevHash()).isEqualTo(rows.get(i - 1).getHash());
        }
        assertThat(rows).allMatch(row -> row.getHash() != null && row.getHash().length() == 64);

        mockMvc.perform(get("/audit/verify")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.valid").value(true))
                .andExpect(jsonPath("$.brokenAtId").doesNotExist());
    }

    @Test
    void tampered_row_is_detected_by_verify() throws Exception {
        failedLogin("no-such-user-tamper-target");
        AuditEvent tampered = auditRepository.findAll().stream()
                .filter(e -> "login".equals(e.getAction()) && "failure".equals(e.getOutcome())
                        && "no-such-user-tamper-target".equals(e.getUsername()))
                .reduce((a, b) -> { throw new AssertionError("more than one matching row"); })
                .orElseThrow(() -> new AssertionError("no matching row"));
        String adminToken = login("admin", "adminpass");

        // Simulate a direct database edit — the one thing the append-only repository
        // interface cannot do itself, which is exactly the threat this chain defends against.
        try (Connection connection = dataSource.getConnection();
             Statement statement = connection.createStatement()) {
            statement.execute("UPDATE audit_event SET outcome = 'success' WHERE id = " + tampered.getId());
        }

        mockMvc.perform(get("/audit/verify")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.valid").value(false))
                .andExpect(jsonPath("$.brokenAtId").value(tampered.getId()));
    }

    @Test
    void audit_verify_endpoint_admin_only() throws Exception {
        String viewerToken = login("viewer", "viewerpass");
        String ownerToken = login("owner", "ownerpass");

        mockMvc.perform(get("/audit/verify")
                        .header("Authorization", "Bearer " + viewerToken))
                .andExpect(status().isForbidden());

        mockMvc.perform(get("/audit/verify")
                        .header("Authorization", "Bearer " + ownerToken))
                .andExpect(status().isForbidden());
    }
}
