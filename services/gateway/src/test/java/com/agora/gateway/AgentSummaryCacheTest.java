package com.agora.gateway;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.github.tomakehurst.wiremock.WireMockServer;
import com.github.tomakehurst.wiremock.core.WireMockConfiguration;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
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
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * The instance listing builds one summary per instance, and each summary costs
 * three upstream calls — so the page every user lands on was O(instances x 3)
 * round trips on every view, getting slower with each mailbox onboarded.
 *
 * Its own class because the cache is disabled everywhere else: a figure cached
 * by one test must never be served to the next.
 *
 * Ordered by method name: the suspend test leaves the seeded instance inactive,
 * and an inactive instance reports a canned summary without calling upstream at
 * all — so the fan-out test has to run first to see a fan-out.
 */
@TestMethodOrder(MethodOrderer.MethodName.class)
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:summarycachedb;DB_CLOSE_DELAY=-1",
        "spring.datasource.driver-class-name=org.h2.Driver",
        "spring.datasource.username=sa",
        "spring.datasource.password=",
        "spring.jpa.database-platform=org.hibernate.dialect.H2Dialect",
        "spring.jpa.hibernate.ddl-auto=create-drop",
        "gateway.upstream.summary-cache-seconds=30",
        "gateway.jwt.secret=test-secret-test-secret-test-secret-0123",
        "gateway.owner.username=owner",
        "gateway.owner.password=ownerpass",
        "gateway.admin.username=admin",
        "gateway.admin.password=adminpass"
})
class AgentSummaryCacheTest {

    private static WireMockServer wireMock;

    @DynamicPropertySource
    static void wireMockProperties(DynamicPropertyRegistry registry) {
        wireMock = new WireMockServer(WireMockConfiguration.wireMockConfig().dynamicPort());
        wireMock.start();
        registry.add("gateway.upstream.email-agent-url", () -> "http://localhost:" + wireMock.port());
    }

    @Autowired private MockMvc mockMvc;
    @Autowired private ObjectMapper objectMapper;

    private String login(String username, String password) throws Exception {
        String body = objectMapper.writeValueAsString(Map.of("username", username, "password", password));
        String response = mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        return objectMapper.readTree(response).get("token").asText();
    }

    private void stubUpstream() {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/health"))
                .willReturn(aResponse().withStatus(200)));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/drafts"))
                .willReturn(aResponse().withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"drafts\":[]}")));
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/costs/summary?period=day"))
                .willReturn(aResponse().withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"totals\":{\"cost_eur\":0}}")));
    }

    @Test
    void a_repeat_listing_does_not_pay_the_upstream_fan_out_again() throws Exception {
        stubUpstream();
        String token = login("owner", "ownerpass");

        mockMvc.perform(get("/agent-instances").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk());
        int afterFirst = wireMock.findAll(getRequestedFor(urlEqualTo("/drafts"))).size();
        assertThat(afterFirst).isPositive();

        mockMvc.perform(get("/agent-instances").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk());

        assertThat(wireMock.findAll(getRequestedFor(urlEqualTo("/drafts"))).size())
                .as("a second listing inside the cache window must not fan out again")
                .isEqualTo(afterFirst);
    }

    @Test
    void suspending_an_instance_shows_immediately_rather_than_when_the_cache_expires() throws Exception {
        stubUpstream();
        String admin = login("admin", "adminpass");

        mockMvc.perform(get("/agent-instances").header("Authorization", "Bearer " + admin))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[?(@.id=='default-email-agent')].status").value("active"));

        mockMvc.perform(post("/agent-instances/default-email-agent/deactivate")
                        .header("Authorization", "Bearer " + admin))
                .andExpect(status().isOk());

        // Without dropping the cached summary the person who just suspended it
        // would keep seeing it reported as running until the window expired.
        mockMvc.perform(get("/agent-instances").header("Authorization", "Bearer " + admin))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[?(@.id=='default-email-agent')].status").value("inactive"))
                .andExpect(jsonPath("$[?(@.id=='default-email-agent')].summary.service_health").value("inactive"));
    }
}
