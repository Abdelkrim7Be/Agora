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
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"author\":\"a@b.com\",\"to\":\"me@b.com\",\"subject\":\"Hi\",\"email_thread\":\"Hello\"}"))
                .andExpect(status().isOk())
                .andExpect(content().json(responseBody));

        wireMock.verify(postRequestedFor(urlEqualTo("/run")));
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
