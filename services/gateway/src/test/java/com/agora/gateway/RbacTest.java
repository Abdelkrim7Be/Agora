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
import static com.github.tomakehurst.wiremock.client.WireMock.deleteRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.postRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.putRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.client.WireMock.urlPathEqualTo;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import static org.junit.jupiter.api.Assertions.assertTrue;

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
        "gateway.viewer.password=viewerpass",
        "gateway.admin.username=admin",
        "gateway.admin.password=adminpass"
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

    @Autowired
    private com.agora.gateway.user.UserRepository userRepository;

    @Autowired
    private org.springframework.security.crypto.password.PasswordEncoder passwordEncoder;

    @Autowired
    private com.agora.gateway.agent.InstanceGrantService instanceGrantService;

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
    void owner_can_summarize() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/run/abc/summarize"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"run_id\":\"abc\",\"summary\":\"tl;dr\"}")));

        mockMvc.perform(post("/api/agent/run/abc/summarize")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.summary").value("tl;dr"));

        wireMock.verify(1, postRequestedFor(urlPathEqualTo("/run/abc/summarize")));
    }

    @Test
    void viewer_without_grant_cannot_summarize_403() throws Exception {
        // Same approve-tier gate as /approve, /reject, /respond: a plain viewer JWT
        // role without a per-instance approver grant is denied at the proxy, same as
        // viewer_cannot_approve_403 above.
        mockMvc.perform(post("/api/agent/run/abc/summarize")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlEqualTo("/run/abc/summarize")));
    }

    @Test
    void anonymous_cannot_summarize_401() throws Exception {
        mockMvc.perform(post("/api/agent/run/abc/summarize"))
                .andExpect(status().isUnauthorized());

        wireMock.verify(0, postRequestedFor(urlEqualTo("/run/abc/summarize")));
    }

    @Test
    void owner_can_request_tone_adjust() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/run/abc/tone"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"run_id\":\"abc\",\"field\":\"content\",\"content\":\"reformulé\"}")));

        mockMvc.perform(post("/api/agent/run/abc/tone")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"tone\":\"formel\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.content").value("reformulé"));

        wireMock.verify(1, postRequestedFor(urlPathEqualTo("/run/abc/tone")));
    }

    @Test
    void viewer_without_grant_cannot_request_tone_adjust_403() throws Exception {
        mockMvc.perform(post("/api/agent/run/abc/tone")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"tone\":\"formel\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlEqualTo("/run/abc/tone")));
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
    void viewer_cannot_prepare_campaign_403() throws Exception {
        mockMvc.perform(post("/api/agent/campaigns/prepare")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"segment_id\":\"team\",\"template_name\":\"announce\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlEqualTo("/campaigns/prepare")));
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
    void owner_cannot_read_cost_summary_403() throws Exception {
        mockMvc.perform(get("/api/agent/costs/summary?period=session")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, getRequestedFor(urlEqualTo("/costs/summary?period=session")));
    }

    @Test
    void admin_can_read_cost_summary() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/costs/summary"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"period\":\"session\"}")));

        mockMvc.perform(get("/api/agent/costs/summary?period=session")
                        .header("Authorization", "Bearer " + login("admin", "adminpass")))
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
    void viewer_can_read_signature() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlEqualTo("/signature"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"enabled\":false}")));

        mockMvc.perform(get("/api/agent/signature")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.enabled").value(false));

        wireMock.verify(1, getRequestedFor(urlEqualTo("/signature")));
    }

    @Test
    void viewer_cannot_update_signature_403() throws Exception {
        mockMvc.perform(put("/api/agent/signature")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"enabled\":true,\"text\":\"Karim\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, putRequestedFor(urlEqualTo("/signature")));
    }






    @Test
    void admin_can_update_signature() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.put(urlEqualTo("/signature"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"enabled\":true}")));

        mockMvc.perform(put("/api/agent/signature")
                        .header("Authorization", "Bearer " + login("admin", "adminpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"enabled\":true,\"text\":\"Karim\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.enabled").value(true));

        wireMock.verify(1, putRequestedFor(urlEqualTo("/signature")));
    }

    @Test
    void viewer_can_read_roles() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/roles"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"roles\":[{\"role_key\":\"hr\",\"emails\":[\"hr@example.com\"]}]}")));

        mockMvc.perform(get("/api/agent/roles")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.roles").isArray());

        wireMock.verify(1, getRequestedFor(urlEqualTo("/roles")));
    }

    @Test
    void viewer_can_read_contacts() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/contacts"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"contacts\":[]}")));

        mockMvc.perform(get("/api/agent/contacts")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.contacts").isArray());

        wireMock.verify(1, getRequestedFor(urlEqualTo("/contacts")));
    }

    @Test
    void viewer_can_read_drafts() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/drafts"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"drafts\":[]}")));

        mockMvc.perform(get("/api/agent/drafts?priority=urgent")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.drafts").isArray());

        wireMock.verify(1, getRequestedFor(urlEqualTo("/drafts?priority=urgent")));
    }

    @Test
    void viewer_cannot_create_role_403() throws Exception {
        mockMvc.perform(post("/api/agent/roles")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"role_key\":\"finance\",\"display_name\":\"Finance\",\"emails\":[\"finance@example.com\"]}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlEqualTo("/roles")));
    }

    @Test
    void viewer_cannot_update_categories_403() throws Exception {
        mockMvc.perform(put("/api/agent/categories")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"categories_yaml\":\"enabled: false\\n\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, putRequestedFor(urlEqualTo("/categories")));
    }

    @Test
    void viewer_cannot_edit_category_403() throws Exception {
        mockMvc.perform(put("/api/agent/categories/support")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"display_name\":\"Support\",\"priority\":\"normal\",\"policy\":\"notify\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, putRequestedFor(urlPathEqualTo("/categories/support")));
    }

    @Test
    void owner_can_edit_category() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.put(urlPathEqualTo("/categories/support"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"parsed\":{}}")));

        mockMvc.perform(put("/api/agent/categories/support")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"display_name\":\"Support\",\"priority\":\"normal\",\"policy\":\"notify\"}"))
                .andExpect(status().isOk());

        wireMock.verify(1, putRequestedFor(urlPathEqualTo("/categories/support")));
    }

    @Test
    void viewer_can_read_junk_settings() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/junk"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"junk\":{\"enabled\":true}}")));

        mockMvc.perform(get("/api/agent/junk")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isOk());

        wireMock.verify(1, getRequestedFor(urlPathEqualTo("/junk")));
    }

    @Test
    void viewer_cannot_change_junk_settings_403() throws Exception {
        mockMvc.perform(put("/api/agent/junk")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"enabled\":false}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, putRequestedFor(urlPathEqualTo("/junk")));
    }

    @Test
    void owner_can_change_junk_settings() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.put(urlPathEqualTo("/junk"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"junk\":{\"enabled\":false}}")));

        mockMvc.perform(put("/api/agent/junk")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"enabled\":false}"))
                .andExpect(status().isOk());

        wireMock.verify(1, putRequestedFor(urlPathEqualTo("/junk")));
    }

    @Test
    void viewer_cannot_add_rule_403() throws Exception {
        mockMvc.perform(post("/api/agent/rules/rule")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"archive promos\",\"then\":{\"archive\":true}}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlEqualTo("/rules/rule")));
    }

    @Test
    void owner_can_add_rule() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/rules/rule"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"parsed\":{}}")));

        mockMvc.perform(post("/api/agent/rules/rule")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"archive promos\",\"then\":{\"archive\":true}}"))
                .andExpect(status().isOk());

        wireMock.verify(1, postRequestedFor(urlPathEqualTo("/rules/rule")));
    }

    @Test
    void owner_can_update_section_config() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.put(urlPathEqualTo("/rules/section-config"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"parsed\":{}}")));

        mockMvc.perform(put("/api/agent/rules/section-config")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"section\":\"digest\",\"config\":{\"hour\":9}}"))
                .andExpect(status().isOk());

        wireMock.verify(1, putRequestedFor(urlPathEqualTo("/rules/section-config")));
    }

    @Test
    void viewer_cannot_delete_rule_403() throws Exception {
        mockMvc.perform(post("/api/agent/rules/rule-delete")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"archive promotions\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlEqualTo("/rules/rule-delete")));
    }

    @Test
    void viewer_cannot_delete_category_403() throws Exception {
        mockMvc.perform(delete("/api/agent/categories/support")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, deleteRequestedFor(urlPathEqualTo("/categories/support")));
    }

    @Test
    void owner_can_delete_category() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.delete(urlPathEqualTo("/categories/support"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"parsed\":{}}")));

        mockMvc.perform(delete("/api/agent/categories/support")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isOk());

        wireMock.verify(1, deleteRequestedFor(urlPathEqualTo("/categories/support")));
    }

    @Test
    void viewer_cannot_duplicate_category_403() throws Exception {
        mockMvc.perform(post("/api/agent/categories/support/duplicate")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlPathEqualTo("/categories/support/duplicate")));
    }

    @Test
    void owner_can_duplicate_category() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/categories/support/duplicate"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"parsed\":{},\"new_name\":\"support_copy\"}")));

        mockMvc.perform(post("/api/agent/categories/support/duplicate")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isOk());

        wireMock.verify(1, postRequestedFor(urlPathEqualTo("/categories/support/duplicate")));
    }

    @Test
    void viewer_cannot_test_match_category_403() throws Exception {
        mockMvc.perform(post("/api/agent/categories/test-match")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"author\":\"a@b.com\",\"subject\":\"hi\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlPathEqualTo("/categories/test-match")));
    }

    @Test
    void owner_can_test_match_category() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/categories/test-match"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"matched\":false}")));

        mockMvc.perform(post("/api/agent/categories/test-match")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"author\":\"a@b.com\",\"subject\":\"hi\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.matched").value(false));

        wireMock.verify(1, postRequestedFor(urlPathEqualTo("/categories/test-match")));
    }

    @Test
    void anonymous_cannot_test_match_category_401() throws Exception {
        mockMvc.perform(post("/api/agent/categories/test-match")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"author\":\"a@b.com\",\"subject\":\"hi\"}"))
                .andExpect(status().isUnauthorized());

        wireMock.verify(0, postRequestedFor(urlPathEqualTo("/categories/test-match")));
    }

    @Test
    void owner_can_create_role() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/roles"))
                .willReturn(aResponse()
                        .withStatus(201)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"role\":{\"role_key\":\"finance\",\"primary_email\":\"finance@example.com\"}}")));

        mockMvc.perform(post("/api/agent/roles")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"role_key\":\"finance\",\"display_name\":\"Finance\",\"emails\":[\"finance@example.com\"]}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.role.role_key").value("finance"));

        wireMock.verify(1, postRequestedFor(urlEqualTo("/roles")));
    }

    @Test
    void viewer_cannot_create_contact_403() throws Exception {
        mockMvc.perform(post("/api/agent/contacts")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"email\":\"a@example.com\",\"audience\":\"client\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlEqualTo("/contacts")));
    }

    @Test
    void owner_can_create_contact() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/contacts"))
                .willReturn(aResponse()
                        .withStatus(201)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"contact\":{\"email\":\"a@example.com\",\"audience\":\"client\"}}")));

        mockMvc.perform(post("/api/agent/contacts")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"email\":\"a@example.com\",\"audience\":\"client\"}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.contact.email").value("a@example.com"));

        wireMock.verify(1, postRequestedFor(urlEqualTo("/contacts")));
    }

    @Test
    void viewer_cannot_delete_contact_403() throws Exception {
        mockMvc.perform(delete("/api/agent/contacts/a@example.com")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, deleteRequestedFor(urlPathEqualTo("/contacts/a@example.com")));
    }

    @Test
    void viewer_cannot_import_contacts_403() throws Exception {
        mockMvc.perform(post("/api/agent/contacts/import")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"csv_text\":\"email,audience\\na@example.com,client\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlEqualTo("/contacts/import")));
    }

    @Test
    void viewer_can_read_segments() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/segments"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"segments\":[]}")));

        mockMvc.perform(get("/api/agent/segments")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.segments").isArray());

        wireMock.verify(1, getRequestedFor(urlEqualTo("/segments")));
    }

    @Test
    void viewer_cannot_create_segment_403() throws Exception {
        mockMvc.perform(post("/api/agent/segments")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"id\":\"seg1\",\"name\":\"Segment 1\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlEqualTo("/segments")));
    }

    @Test
    void owner_can_create_segment() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/segments"))
                .willReturn(aResponse()
                        .withStatus(201)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"segment\":{\"id\":\"seg1\",\"name\":\"Segment 1\"}}")));

        mockMvc.perform(post("/api/agent/segments")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"id\":\"seg1\",\"name\":\"Segment 1\"}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.segment.id").value("seg1"));

        wireMock.verify(1, postRequestedFor(urlEqualTo("/segments")));
    }

    @Test
    void viewer_cannot_delete_segment_403() throws Exception {
        mockMvc.perform(delete("/api/agent/segments/seg1")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, deleteRequestedFor(urlPathEqualTo("/segments/seg1")));
    }

    @Test
    void viewer_can_read_agent_health() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/health"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"status\":\"ok\",\"agent\":{\"status\":\"up\"},\"queue_depth\":0}")));

        mockMvc.perform(get("/api/agent/health")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.agent.status").value("up"));

        wireMock.verify(1, getRequestedFor(urlEqualTo("/health")));
    }

    @Test
    void unauthenticated_cannot_read_agent_health_401() throws Exception {
        mockMvc.perform(get("/api/agent/health"))
                .andExpect(status().isUnauthorized());

        wireMock.verify(0, getRequestedFor(urlEqualTo("/health")));
    }

    @Test
    void admin_can_list_users() throws Exception {
        mockMvc.perform(get("/users")
                        .header("Authorization", "Bearer " + login("admin", "adminpass")))
                .andExpect(status().isOk());
    }

    @Test
    void owner_cannot_list_users_403() throws Exception {
        mockMvc.perform(get("/users")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isForbidden());
    }

    @Test
    void viewer_cannot_create_user_403() throws Exception {
        mockMvc.perform(post("/users")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"hrapprover\",\"password\":\"pw123456\",\"role\":\"approver\",\"department\":\"HR\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));
    }

    @Test
    void admin_can_create_user_with_role_and_department() throws Exception {
        mockMvc.perform(post("/users")
                        .header("Authorization", "Bearer " + login("admin", "adminpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"hrapprover\",\"password\":\"pw123456\",\"role\":\"approver\",\"department\":\"HR\"}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.username").value("hrapprover"))
                .andExpect(jsonPath("$.role").value("approver"))
                .andExpect(jsonPath("$.department").value("HR"))
                .andExpect(jsonPath("$.enabled").value(true))
                .andExpect(jsonPath("$.passwordHash").doesNotExist());
    }

    @Test
    void admin_disable_prevents_login() throws Exception {
        String adminToken = login("admin", "adminpass");
        mockMvc.perform(post("/users")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"tempstaff\",\"password\":\"pw123456\",\"role\":\"viewer\"}"))
                .andExpect(status().isCreated());

        var user = userRepository.findByUsername("tempstaff").orElseThrow();
        mockMvc.perform(post("/users/" + user.getId() + "/disable")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.enabled").value(false));

        mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"tempstaff\",\"password\":\"pw123456\"}"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void admin_cannot_disable_current_user() throws Exception {
        String adminToken = login("admin", "adminpass");
        var admin = userRepository.findByUsername("admin").orElseThrow();

        mockMvc.perform(post("/users/" + admin.getId() + "/disable")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isBadRequest());

        assertTrue(userRepository.findByUsername("admin").orElseThrow().isEnabled());
    }


    @Test
    void approver_role_passes_approve_route_gate() throws Exception {
        // A global "approver" JWT role must clear the SecurityConfig JWT gate on
        // /approve (the slice-05 TODO this slice resolves), then the existing
        // instance-grant model (unchanged) authorizes the actual send.
        userRepository.save(new com.agora.gateway.user.AppUser(
                "hrapprover2", passwordEncoder.encode("pw123456"), "approver", "HR"));
        instanceGrantService.addGrant("default-email-agent", "hrapprover2", "approver", "admin");

        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/run/abc/approve"))
                .willReturn(aResponse().withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"run_id\":\"abc\",\"status\":\"completed\"}")));

        mockMvc.perform(post("/api/agent/run/abc/approve")
                        .header("Authorization", "Bearer " + login("hrapprover2", "pw123456"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isOk());

        wireMock.verify(1, postRequestedFor(urlPathEqualTo("/run/abc/approve")));
    }

    @Test
    void approver_without_grant_still_403_at_instance_level() throws Exception {
        // Clearing the JWT gate is not enough: without an instance grant (and the
        // default instance's allowedRoles = "owner" only), ProxyController still denies.
        userRepository.save(new com.agora.gateway.user.AppUser(
                "hrapprover3", passwordEncoder.encode("pw123456"), "approver", "HR"));

        mockMvc.perform(post("/api/agent/run/abc/approve")
                        .header("Authorization", "Bearer " + login("hrapprover3", "pw123456"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isForbidden());

        wireMock.verify(0, postRequestedFor(urlPathEqualTo("/run/abc/approve")));
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

    @Test
    void unauthenticated_denied_on_unenumerated_route_401() throws Exception {
        mockMvc.perform(post("/api/agent/run/abc/unknownverb")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("unauthorized"));

        wireMock.verify(0, postRequestedFor(urlEqualTo("/run/abc/unknownverb")));
    }

    @Test
    void viewer_can_read_analytics() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/analytics"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"handled\":0}")));

        mockMvc.perform(get("/api/agent/analytics?period=week")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isOk());

        wireMock.verify(1, getRequestedFor(urlPathEqualTo("/analytics")));
    }

    @Test
    void owner_can_update_alerts_settings() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.put(urlPathEqualTo("/alerts/settings"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"ok\":true}")));

        mockMvc.perform(put("/api/agent/alerts/settings")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isOk());

        wireMock.verify(1, putRequestedFor(urlPathEqualTo("/alerts/settings")));
    }

    @Test
    void viewer_cannot_run_retention_403() throws Exception {
        mockMvc.perform(post("/api/agent/retention/run")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlPathEqualTo("/retention/run")));
    }

    @Test
    void owner_can_claim_approval() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/inbox/abc/claim"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"ok\":true}")));

        mockMvc.perform(post("/api/agent/inbox/abc/claim")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isOk());

        wireMock.verify(1, postRequestedFor(urlPathEqualTo("/inbox/abc/claim")));
    }

    @Test
    void admin_can_list_agent_instances() throws Exception {
        mockMvc.perform(get("/agent-instances")
                        .header("Authorization", "Bearer " + login("admin", "adminpass")))
                .andExpect(status().isOk());
    }

    @Test
    void admin_can_read_inbox() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/inbox"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("[]")));

        mockMvc.perform(get("/api/agent/inbox?limit=25")
                        .header("Authorization", "Bearer " + login("admin", "adminpass")))
                .andExpect(status().isOk());

        wireMock.verify(1, getRequestedFor(urlPathEqualTo("/inbox")));
    }

    @Test
    void viewer_can_read_send_mode() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/send-mode"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"send_mode\":\"simulation\"}")));

        mockMvc.perform(get("/api/agent/send-mode")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.send_mode").value("simulation"));

        wireMock.verify(1, getRequestedFor(urlPathEqualTo("/send-mode")));
    }

    @Test
    void viewer_cannot_update_send_mode_403() throws Exception {
        mockMvc.perform(put("/api/agent/send-mode")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"send_mode\":\"live\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, putRequestedFor(urlPathEqualTo("/send-mode")));
    }

    @Test
    void owner_can_update_send_mode() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.put(urlPathEqualTo("/send-mode"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"send_mode\":\"live\"}")));

        mockMvc.perform(put("/api/agent/send-mode")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"send_mode\":\"live\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.send_mode").value("live"));

        wireMock.verify(1, putRequestedFor(urlPathEqualTo("/send-mode")));
    }

    @Test
    void viewer_can_read_persona() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/persona"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"mission\":\"\"}")));

        mockMvc.perform(get("/api/agent/persona")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isOk());

        wireMock.verify(1, getRequestedFor(urlPathEqualTo("/persona")));
    }

    @Test
    void viewer_cannot_update_persona_403() throws Exception {
        mockMvc.perform(put("/api/agent/persona")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, putRequestedFor(urlPathEqualTo("/persona")));
    }

    @Test
    void owner_can_suggest_persona() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/persona/suggest"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"suggestion\":{}}")));

        mockMvc.perform(post("/api/agent/persona/suggest")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isOk());

        wireMock.verify(1, postRequestedFor(urlPathEqualTo("/persona/suggest")));
    }

    @Test
    void viewer_cannot_suggest_persona_403() throws Exception {
        mockMvc.perform(post("/api/agent/persona/suggest")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("forbidden"));

        wireMock.verify(0, postRequestedFor(urlPathEqualTo("/persona/suggest")));
    }

    @Test
    void owner_can_upload_signature_image() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/signature/image"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"stored\":true}")));

        mockMvc.perform(post("/api/agent/signature/image")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.parseMediaType("multipart/form-data; boundary=agora"))
                        .content("--agora\r\nContent-Disposition: form-data; name=\"file\"; filename=\"logo.png\"\r\nContent-Type: image/png\r\n\r\nfake-image-bytes\r\n--agora--\r\n".getBytes()))
                .andExpect(status().isOk());

        wireMock.verify(1, postRequestedFor(urlPathEqualTo("/signature/image")));
    }

    @Test
    void oversized_proxy_body_rejected_413() throws Exception {
        byte[] huge = new byte[2 * 1024 * 1024 + 1];
        mockMvc.perform(post("/api/agent/signature/image")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.parseMediaType("multipart/form-data; boundary=agora"))
                        .content(huge))
                .andExpect(status().isPayloadTooLarge());

        wireMock.verify(0, postRequestedFor(urlPathEqualTo("/signature/image")));
    }

    @Test
    void viewer_cannot_upload_signature_image_403() throws Exception {
        mockMvc.perform(post("/api/agent/signature/image")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.MULTIPART_FORM_DATA)
                        .content("fake-image-bytes".getBytes()))
                .andExpect(status().isForbidden());

        wireMock.verify(0, postRequestedFor(urlPathEqualTo("/signature/image")));
    }

    @Test
    void viewer_can_read_memory_summary() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/memory/summary"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"triage_preferences\":[]}")));

        mockMvc.perform(get("/api/agent/memory/summary")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isOk());

        wireMock.verify(1, getRequestedFor(urlPathEqualTo("/memory/summary")));
    }

    @Test
    void viewer_cannot_delete_memory_item_403() throws Exception {
        mockMvc.perform(delete("/api/agent/memory/item?kind=triage_preferences&id=abc")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isForbidden());

        wireMock.verify(0, deleteRequestedFor(urlPathEqualTo("/memory/item")));
    }

    @Test
    void owner_can_delete_memory_item() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.delete(urlPathEqualTo("/memory/item"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"removed\":true}")));

        mockMvc.perform(delete("/api/agent/memory/item?kind=triage_preferences&id=abc")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass")))
                .andExpect(status().isOk());

        wireMock.verify(1, deleteRequestedFor(urlPathEqualTo("/memory/item")));
    }

    @Test
    void owner_can_bulk_decide() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/runs/bulk"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"results\":[]}")));

        mockMvc.perform(post("/api/agent/runs/bulk")
                        .header("Authorization", "Bearer " + login("owner", "ownerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"run_ids\":[\"r1\"],\"decision\":\"approve\"}"))
                .andExpect(status().isOk());

        wireMock.verify(1, postRequestedFor(urlPathEqualTo("/runs/bulk")));
    }

    @Test
    void viewer_cannot_bulk_decide_403() throws Exception {
        mockMvc.perform(post("/api/agent/runs/bulk")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"run_ids\":[\"r1\"],\"decision\":\"approve\"}"))
                .andExpect(status().isForbidden());

        wireMock.verify(0, postRequestedFor(urlPathEqualTo("/runs/bulk")));
    }

    @Test
    void viewer_can_read_contact_photo() throws Exception {
        wireMock.stubFor(com.github.tomakehurst.wiremock.client.WireMock.get(urlPathEqualTo("/contacts/alice@example.com/photo"))
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "image/jpeg").withBody("img")));

        mockMvc.perform(get("/api/agent/contacts/alice@example.com/photo")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isOk());

        wireMock.verify(1, getRequestedFor(urlPathEqualTo("/contacts/alice@example.com/photo")));
    }

    @Test
    void viewer_cannot_upload_contact_photo_403() throws Exception {
        mockMvc.perform(post("/api/agent/contacts/alice@example.com/photo")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.parseMediaType("multipart/form-data; boundary=agora"))
                        .content("--agora--\r\n".getBytes()))
                .andExpect(status().isForbidden());

        wireMock.verify(0, postRequestedFor(urlPathEqualTo("/contacts/alice@example.com/photo")));
    }
}
