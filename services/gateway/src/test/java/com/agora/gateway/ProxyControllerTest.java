package com.agora.gateway;

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
import static com.github.tomakehurst.wiremock.client.WireMock.equalTo;
import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.postRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.client.WireMock.urlPathEqualTo;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.hamcrest.Matchers.containsString;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:proxydb;DB_CLOSE_DELAY=-1",
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
        "gateway.admin.username=admin",
        "gateway.admin.password=adminpass"
})
class ProxyControllerTest {

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

    private String ownerToken() throws Exception {
        String body = objectMapper.writeValueAsString(Map.of("username", "owner", "password", "ownerpass"));
        String response = mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        return objectMapper.readTree(response).get("token").asText();
    }

    private String adminToken() throws Exception {
        String body = objectMapper.writeValueAsString(Map.of("username", "admin", "password", "adminpass"));
        String response = mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        return objectMapper.readTree(response).get("token").asText();
    }

    @Test
    void health_returns_ok() throws Exception {
        mockMvc.perform(get("/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("ok"));
    }

    @Test
    void proxy_post_run_forwards_to_upstream_and_returns_response() throws Exception {
        String responseBody = "{\"run_id\":\"abc123\",\"status\":\"completed\"}";

        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlEqualTo("/run"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(responseBody)));

        mockMvc.perform(post("/api/agent/run")
                        .header("Authorization", "Bearer " + ownerToken())
                        .header("X-Agora-User", "spoofed-user")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"author\":\"a@b.com\",\"to\":\"me@b.com\",\"subject\":\"Hi\",\"email_thread\":\"Hello\"}"))
                .andExpect(status().isOk())
                .andExpect(content().json(responseBody));

        wireMock.verify(postRequestedFor(urlEqualTo("/run"))
                .withHeader("X-Agora-User", equalTo("owner"))
                .withHeader("X-Agora-Agent-Instance", equalTo("default-email-agent")));
    }


    @Test
    void proxy_refuses_an_instance_the_caller_has_no_grant_on() throws Exception {
        // Regression: instances were created with allowed_roles="owner", and
        // effectiveRole() treated allowed_roles as a grant. Any account holding the
        // global "owner" role could therefore read any other account's mailbox by
        // naming it in X-Agora-Agent-Instance — confirmed against a live stack,
        // returning another tenant's real inbox messages.
        String body = objectMapper.writeValueAsString(Map.of(
                "id", "private-mailbox",
                "agent_type", "email-agent",
                "display_name", "Private Mailbox"
        ));
        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + adminToken())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated());

        mockMvc.perform(get("/api/agent/runs")
                        .header("Authorization", "Bearer " + ownerToken())
                        .header("X-Agora-Agent-Instance", "private-mailbox"))
                .andExpect(status().isForbidden());
    }

    @Test
    void proxy_forwards_selected_visible_agent_instance() throws Exception {
        String responseBody = "{\"run_id\":\"abc123\",\"status\":\"completed\"}";

        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlEqualTo("/run"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(responseBody)));

        String token = ownerToken();
        String body = objectMapper.writeValueAsString(Map.of(
                "id", "ceo-email-agent",
                "agent_type", "email-agent",
                "display_name", "CEO Email Agent"
        ));
        mockMvc.perform(post("/agent-instances")
                        .header("Authorization", "Bearer " + adminToken())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated());

        // Instances are private to their creator now, so the owner reaching this
        // admin-created instance needs an explicit grant. Without one the request
        // is refused — see proxy_refuses_an_instance_the_caller_has_no_grant_on.
        mockMvc.perform(post("/agent-instances/ceo-email-agent/grants")
                        .header("Authorization", "Bearer " + adminToken())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"user_id\":\"owner\",\"role\":\"owner\"}"))
                .andExpect(status().is2xxSuccessful());

        mockMvc.perform(post("/api/agent/run")
                        .header("Authorization", "Bearer " + token)
                        .header("X-Agora-Agent-Instance", "ceo-email-agent")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"author\":\"a@b.com\",\"to\":\"me@b.com\",\"subject\":\"Hi\",\"email_thread\":\"Hello\"}"))
                .andExpect(status().isOk())
                .andExpect(content().json(responseBody));

        wireMock.verify(postRequestedFor(urlEqualTo("/run"))
                .withHeader("X-Agora-Agent-Instance", equalTo("ceo-email-agent")));
    }

    @Test
    void admin_proxy_access_is_visible_to_instance_owner() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/inbox"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"messages\":[]}")));

        mockMvc.perform(post("/agent-instances/default-email-agent/grants")
                        .header("Authorization", "Bearer " + ownerToken())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("user_id", "admin", "role", "viewer"))))
                .andExpect(status().isCreated());

        mockMvc.perform(get("/api/agent/inbox")
                        .header("Authorization", "Bearer " + adminToken())
                        .header("X-Agora-Agent-Instance", "default-email-agent"))
                .andExpect(status().isOk());

        assertThat(auditRepository.findAll()).anyMatch(event ->
                "admin_mailbox_access".equals(event.getAction())
                        && "/agent-instances/default-email-agent".equals(event.getPath())
                        && "admin".equals(event.getUsername())
                        && Integer.valueOf(200).equals(event.getUpstreamStatus()));

        mockMvc.perform(get("/agent-instances/default-email-agent/admin-access")
                        .header("Authorization", "Bearer " + ownerToken()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].username").value("admin"))
                .andExpect(jsonPath("$[0].method").value("GET"))
                .andExpect(jsonPath("$[0].upstream_status").value(200))
                .andExpect(jsonPath("$[0].outcome").value("GET /api/agent/inbox"));
    }

    @Test
    void proxy_rejects_unknown_agent_instance_header() throws Exception {
        mockMvc.perform(post("/api/agent/run")
                        .header("Authorization", "Bearer " + ownerToken())
                        .header("X-Agora-Agent-Instance", "missing-agent")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlEqualTo("/run")));
    }

    @Test
    void proxy_sync_forwards_to_upstream_and_returns_response() throws Exception {
        String responseBody = "{\"outcomes\":[[\"msg-1\",\"pending_approval\",\"run-1\"]]}";

        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlEqualTo("/sync?limit=7"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(responseBody)));

        mockMvc.perform(post("/api/agent/sync?limit=7")
                        .header("Authorization", "Bearer " + ownerToken()))
                .andExpect(status().isOk())
                .andExpect(content().json(responseBody));

        wireMock.verify(postRequestedFor(urlEqualTo("/sync?limit=7"))
                .withHeader("X-Agora-User", equalTo("owner"))
                .withHeader("X-Agora-Agent-Instance", equalTo("default-email-agent")));
    }


    @Test
    void proxy_run_stream_preserves_sse_content_type_and_body() throws Exception {
        String responseBody = "event: draft\ndata: {\"content\":\"Bonjour\"}\n\nevent: result\ndata: {\"status\":\"pending_approval\"}\n\n";

        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlEqualTo("/run/stream"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "text/event-stream")
                        .withBody(responseBody)));

        mockMvc.perform(post("/api/agent/run/stream")
                        .header("Authorization", "Bearer " + ownerToken())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"author\":\"a@b.com\",\"to\":\"me@b.com\",\"subject\":\"Hi\",\"email_thread\":\"Hello\"}"))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.TEXT_EVENT_STREAM))
                .andExpect(content().string(containsString("event: draft")))
                .andExpect(content().string(containsString("event: result")));

        wireMock.verify(postRequestedFor(urlEqualTo("/run/stream"))
                .withHeader("X-Agora-User", equalTo("owner"))
                .withHeader("X-Agora-Agent-Instance", equalTo("default-email-agent")));
    }

    @Test
    void proxy_campaign_preview_forwards_to_upstream() throws Exception {
        String responseBody = "{\"segment_id\":\"team\",\"recipient_count\":2,\"audience_match\":true}";

        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlEqualTo("/campaigns/preview"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(responseBody)));

        mockMvc.perform(post("/api/agent/campaigns/preview")
                        .header("Authorization", "Bearer " + ownerToken())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"segment_id\":\"team\",\"template_name\":\"announce\"}"))
                .andExpect(status().isOk())
                .andExpect(content().json(responseBody));

        wireMock.verify(postRequestedFor(urlEqualTo("/campaigns/preview"))
                .withHeader("X-Agora-User", equalTo("owner"))
                .withHeader("X-Agora-Agent-Instance", equalTo("default-email-agent")));
    }

    @Test
    void proxy_gmail_oauth_start_requires_owner_and_forwards() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/agent-instances/default-email-agent/connect/gmail/start"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"authorization_url\":\"https://google.example\",\"agent_instance_id\":\"default-email-agent\",\"scopes\":[]}")));

        mockMvc.perform(get("/api/agent/agent-instances/default-email-agent/connect/gmail/start")
                        .header("Authorization", "Bearer " + ownerToken()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.authorization_url").value("https://google.example"));

        wireMock.verify(getRequestedFor(urlEqualTo("/agent-instances/default-email-agent/connect/gmail/start"))
                .withHeader("X-Agora-Agent-Instance", equalTo("default-email-agent")));
    }

    @Test
    void proxy_gmail_oauth_callback_forwards_without_jwt() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/connect/gmail/callback?code=abc&state=signed"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"status\":\"connected\"}")));

        mockMvc.perform(get("/api/agent/connect/gmail/callback?code=abc&state=signed"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("connected"));

        wireMock.verify(getRequestedFor(urlEqualTo("/connect/gmail/callback?code=abc&state=signed")));
    }

    @Test
    void proxy_outlook_oauth_start_requires_a_role_and_forwards() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/agent-instances/default-email-agent/connect/outlook/start"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"authorization_url\":\"https://login.microsoftonline.example\",\"agent_instance_id\":\"default-email-agent\",\"scopes\":[]}")));

        mockMvc.perform(get("/api/agent/agent-instances/default-email-agent/connect/outlook/start")
                        .header("Authorization", "Bearer " + ownerToken()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.authorization_url").value("https://login.microsoftonline.example"));

        wireMock.verify(getRequestedFor(urlEqualTo("/agent-instances/default-email-agent/connect/outlook/start"))
                .withHeader("X-Agora-Agent-Instance", equalTo("default-email-agent")));
    }

    @Test
    void proxy_outlook_oauth_start_rejects_an_anonymous_caller() throws Exception {
        mockMvc.perform(get("/api/agent/agent-instances/default-email-agent/connect/outlook/start"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void proxy_outlook_oauth_callback_forwards_without_jwt() throws Exception {
        // Microsoft redirects the browser here with no Bearer token; the signed
        // OAuth state is what authenticates it, same as the Gmail callback.
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/connect/outlook/callback?code=abc&state=signed"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"status\":\"connected\"}")));

        mockMvc.perform(get("/api/agent/connect/outlook/callback?code=abc&state=signed"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("connected"));

        wireMock.verify(getRequestedFor(urlEqualTo("/connect/outlook/callback?code=abc&state=signed")));
    }

    @Test
    void proxy_connect_test_requires_a_role_and_forwards() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlEqualTo("/connect/test"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"ok\":true,\"provider\":\"gmail\",\"mailbox\":\"ceo@example.com\",\"error\":\"\"}")));

        mockMvc.perform(post("/api/agent/connect/test")
                        .header("Authorization", "Bearer " + ownerToken()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.mailbox").value("ceo@example.com"));

        mockMvc.perform(post("/api/agent/connect/test"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void proxy_gmail_webhook_forwards_without_jwt() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/webhooks/gmail"))
                .willReturn(aResponse()
                        .withStatus(202)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"accepted\":true}")));

        mockMvc.perform(post("/api/agent/webhooks/gmail?token=secret")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"message\":{}}"))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.accepted").value(true));

        wireMock.verify(postRequestedFor(urlPathEqualTo("/webhooks/gmail"))
                .withHeader("X-Agora-Agent-Instance", equalTo("default-email-agent")));
    }

    @Test
    void proxy_passes_upstream_4xx_status_through() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlEqualTo("/run"))
                .willReturn(aResponse()
                        .withStatus(422)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"detail\":\"Validation error\"}")));

        mockMvc.perform(post("/api/agent/run")
                        .header("Authorization", "Bearer " + ownerToken())
                        .header("X-Agora-User", "spoofed-user")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isUnprocessableEntity());
    }

    @Test
    void proxy_get_run_forwards_path_to_upstream() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/run/abc123"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"run_id\":\"abc123\",\"status\":\"pending_approval\"}")));

        mockMvc.perform(get("/api/agent/run/abc123")
                        .header("Authorization", "Bearer " + ownerToken()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.run_id").value("abc123"));

        wireMock.verify(getRequestedFor(urlPathEqualTo("/run/abc123")));
    }
}
