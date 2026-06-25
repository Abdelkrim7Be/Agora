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
import static com.github.tomakehurst.wiremock.client.WireMock.postRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.client.WireMock.urlPathEqualTo;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
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
        "gateway.jwt.secret=test-secret-test-secret-test-secret-0123",
        "gateway.default-agent-instance=default-email-agent",
        "gateway.owner.username=owner",
        "gateway.owner.password=ownerpass"
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
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated());

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
