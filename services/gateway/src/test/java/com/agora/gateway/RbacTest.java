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
}
