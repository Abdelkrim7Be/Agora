package com.agora.gateway;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.github.tomakehurst.wiremock.WireMockServer;
import com.github.tomakehurst.wiremock.core.WireMockConfiguration;
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

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Everyone owns their own profile and password; only an administrator reaches
 * anyone else's. The account is always resolved from the token, so these tests
 * care most about who is *refused*.
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:accountdb;DB_CLOSE_DELAY=-1",
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
class AccountSelfServiceTest {

    private static WireMockServer wireMock;

    @DynamicPropertySource
    static void wireMockProperties(DynamicPropertyRegistry registry) {
        wireMock = new WireMockServer(WireMockConfiguration.wireMockConfig().dynamicPort());
        wireMock.start();
        registry.add("gateway.upstream.email-agent-url", () -> "http://localhost:" + wireMock.port());
    }

    @Autowired private MockMvc mockMvc;
    @Autowired private ObjectMapper objectMapper;

    /** A throwaway account for the destructive password test, so rotating it
     * cannot lock the other tests out of the seeded ones. */
    @BeforeEach
    void ensureThrowawayAccount() throws Exception {
        String admin = login("admin", "adminpass");
        String existing = mockMvc.perform(get("/users").header("Authorization", "Bearer " + admin))
                .andReturn().getResponse().getContentAsString();
        if (existing.contains("\"approverless\"")) return;
        mockMvc.perform(post("/users").header("Authorization", "Bearer " + admin)
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(Map.of(
                        "username", "approverless",
                        "role", "viewer",
                        "password", "approverless-pass"))));
    }

    private String login(String username, String password) throws Exception {
        String body = objectMapper.writeValueAsString(Map.of("username", username, "password", password));
        String response = mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        return objectMapper.readTree(response).get("token").asText();
    }

    private long idOf(String username) throws Exception {
        String body = mockMvc.perform(get("/users")
                        .header("Authorization", "Bearer " + login("admin", "adminpass")))
                .andReturn().getResponse().getContentAsString();
        var node = objectMapper.readTree(body);
        for (var user : node) {
            if (username.equals(user.get("username").asText())) return user.get("id").asLong();
        }
        throw new IllegalStateException("no such user: " + username);
    }

    @Test
    void a_viewer_can_read_its_own_profile() throws Exception {
        mockMvc.perform(get("/me").header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.username").value("viewer"))
                .andExpect(jsonPath("$.role").value("viewer"));
    }

    @Test
    void anonymous_cannot_read_a_profile() throws Exception {
        mockMvc.perform(get("/me")).andExpect(status().isUnauthorized());
    }

    @Test
    void a_viewer_can_edit_its_own_display_name_and_email() throws Exception {
        String token = login("viewer", "viewerpass");
        String body = objectMapper.writeValueAsString(
                Map.of("displayName", "Vue Lecteur", "email", "vue@example.com"));

        mockMvc.perform(put("/me").header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.displayName").value("Vue Lecteur"))
                .andExpect(jsonPath("$.email").value("vue@example.com"));
    }

    @Test
    void editing_your_own_profile_cannot_promote_you() throws Exception {
        String token = login("viewer", "viewerpass");
        // role is deliberately not part of the request record, so a caller sending
        // it changes nothing at all.
        String body = objectMapper.writeValueAsString(Map.of("displayName", "Vue", "role", "admin"));

        mockMvc.perform(put("/me").header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.role").value("viewer"));
    }

    @Test
    void changing_your_own_password_requires_the_current_one() throws Exception {
        String token = login("owner", "ownerpass");
        String body = objectMapper.writeValueAsString(
                Map.of("currentPassword", "not-the-password", "newPassword", "rotated-test-password-1"));

        mockMvc.perform(put("/me/password").header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isBadRequest());
    }

    @Test
    void a_short_password_is_refused() throws Exception {
        String token = login("owner", "ownerpass");
        String body = objectMapper.writeValueAsString(
                Map.of("currentPassword", "ownerpass", "newPassword", "short"));

        mockMvc.perform(put("/me/password").header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isBadRequest());
    }

    @Test
    void changing_your_own_password_then_logging_in_with_it() throws Exception {
        // The seeded accounts are shared by every test in this class, so this one
        // puts the password back before it returns.
        String token = login("approverless", "approverless-pass");
        String body = objectMapper.writeValueAsString(
                Map.of("currentPassword", "approverless-pass", "newPassword", "rotated-test-password-1"));

        mockMvc.perform(put("/me/password").header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk());

        // The old one must stop working, and the new one must start.
        mockMvc.perform(post("/auth/login").contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(
                                Map.of("username", "approverless", "password", "approverless-pass"))))
                .andExpect(status().isUnauthorized());
        login("approverless", "rotated-test-password-1");
    }

    @Test
    void an_admin_can_set_another_accounts_password() throws Exception {
        long targetId = idOf("approverless");
        String body = objectMapper.writeValueAsString(Map.of("password", "resetByAdmin2026"));

        mockMvc.perform(put("/users/" + targetId + "/password")
                        .header("Authorization", "Bearer " + login("admin", "adminpass"))
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk());

        login("approverless", "resetByAdmin2026");
    }

    @Test
    void a_non_admin_cannot_set_another_accounts_password() throws Exception {
        long adminId = idOf("admin");
        String body = objectMapper.writeValueAsString(Map.of("password", "takeoverAttempt2026"));

        mockMvc.perform(put("/users/" + adminId + "/password")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass"))
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isForbidden());
    }

    @Test
    void an_admin_can_clear_a_locked_out_accounts_second_factor() throws Exception {
        // Every other MFA route proves possession of the device, so losing the
        // device meant losing the account. This is the only way back in.
        long targetId = idOf("approverless");

        mockMvc.perform(post("/users/" + targetId + "/mfa/reset")
                        .header("Authorization", "Bearer " + login("admin", "adminpass")))
                .andExpect(status().isOk());

        String body = mockMvc.perform(get("/users")
                        .header("Authorization", "Bearer " + login("admin", "adminpass")))
                .andReturn().getResponse().getContentAsString();
        for (var user : objectMapper.readTree(body)) {
            if ("approverless".equals(user.get("username").asText())) {
                assertThat(user.get("mfaEnabled").asBoolean()).isFalse();
            }
        }
    }

    @Test
    void a_non_admin_cannot_clear_someone_elses_second_factor() throws Exception {
        long adminId = idOf("admin");

        mockMvc.perform(post("/users/" + adminId + "/mfa/reset")
                        .header("Authorization", "Bearer " + login("viewer", "viewerpass")))
                .andExpect(status().isForbidden());
    }
}
