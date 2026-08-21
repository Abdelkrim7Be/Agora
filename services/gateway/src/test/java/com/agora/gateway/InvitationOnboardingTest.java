package com.agora.gateway;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import com.agora.gateway.audit.AuditEvent;
import com.agora.gateway.audit.AuditRepository;
import com.agora.gateway.user.UserInvitation;
import com.agora.gateway.user.UserInvitationRepository;
import com.agora.gateway.user.UserRepository;

import java.net.URI;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.not;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:invitedb;DB_CLOSE_DELAY=-1",
        "spring.datasource.driver-class-name=org.h2.Driver",
        "spring.datasource.username=sa",
        "spring.datasource.password=",
        "spring.jpa.database-platform=org.hibernate.dialect.H2Dialect",
        "spring.jpa.hibernate.ddl-auto=create-drop",
        "gateway.jwt.secret=test-secret-test-secret-test-secret-0123",
        "gateway.owner.username=owner",
        "gateway.owner.password=ownerpass",
        "gateway.admin.username=admin",
        "gateway.admin.password=adminpass",
        "gateway.app-url=https://app.example.test"
})
class InvitationOnboardingTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private UserInvitationRepository invitations;

    @Autowired
    private UserRepository users;

    @Autowired
    private AuditRepository auditRepository;

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
    void admin_can_invite_user_and_invitee_sets_password_once() throws Exception {
        String adminToken = login("admin", "adminpass");
        String createdBody = mockMvc.perform(post("/users")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "username", "invited@example.test",
                                "email", "person@example.test",
                                "role", "viewer"
                        ))))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.username").value("invited@example.test"))
                .andExpect(jsonPath("$.email").value("person@example.test"))
                .andExpect(jsonPath("$.enabled").value(false))
                .andExpect(jsonPath("$.invitationSent").value(false))
                .andExpect(jsonPath("$.setupLink").value(containsString("https://app.example.test/invite/")))
                .andExpect(jsonPath("$.passwordHash").doesNotExist())
                .andReturn().getResponse().getContentAsString();

        String token = tokenFromSetupLink(objectMapper.readTree(createdBody).get("setupLink").asText());

        mockMvc.perform(get("/auth/invite/" + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.valid").value(true))
                .andExpect(jsonPath("$.username").value("invited@example.test"));

        mockMvc.perform(post("/auth/invite/" + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("password", "new-password-123"))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.ok").value(true));

        login("invited@example.test", "new-password-123");

        mockMvc.perform(post("/auth/invite/" + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("password", "another-password-123"))))
                .andExpect(status().isBadRequest());
    }

    @Test
    void resending_invitation_retires_previous_token() throws Exception {
        String adminToken = login("admin", "adminpass");
        JsonNode created = objectMapper.readTree(mockMvc.perform(post("/users")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "username", "resend@example.test",
                                "role", "viewer"
                        ))))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString());
        long userId = created.get("id").asLong();
        String oldToken = tokenFromSetupLink(created.get("setupLink").asText());

        JsonNode resent = objectMapper.readTree(mockMvc.perform(post("/users/" + userId + "/invite")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.setupLink").value(not(created.get("setupLink").asText())))
                .andReturn().getResponse().getContentAsString());
        String newToken = tokenFromSetupLink(resent.get("setupLink").asText());

        mockMvc.perform(get("/auth/invite/" + oldToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.valid").value(false));
        mockMvc.perform(get("/auth/invite/" + newToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.valid").value(true));
    }

    @Test
    void resending_invitation_can_override_the_destination_address() throws Exception {
        String adminToken = login("admin", "adminpass");
        JsonNode created = objectMapper.readTree(mockMvc.perform(post("/users")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "username", "typo@example.test",
                                "role", "viewer",
                                "email", "typoo@example.test"
                        ))))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString());
        long userId = created.get("id").asLong();

        mockMvc.perform(post("/users/" + userId + "/invite")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("email", "typo@example.test"))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.email").value("typo@example.test"));

        mockMvc.perform(get("/users")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[?(@.id == " + userId + ")].email").value(org.hamcrest.Matchers.contains("typo@example.test")));
    }

    @Test
    void enabled_accounts_do_not_expose_invitation_expiry_in_the_user_list() throws Exception {
        String adminToken = login("admin", "adminpass");
        JsonNode created = objectMapper.readTree(mockMvc.perform(post("/users")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "username", "configured@example.test",
                                "role", "viewer"
                        ))))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.enabled").value(false))
                .andExpect(jsonPath("$.invitationExpiresAt").exists())
                .andReturn().getResponse().getContentAsString());
        long userId = created.get("id").asLong();

        mockMvc.perform(post("/users/" + userId + "/enable")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.enabled").value(true))
                .andExpect(jsonPath("$.pendingInvitation").value(false))
                .andExpect(jsonPath("$.invitationExpiresAt").isEmpty());

        JsonNode listed = objectMapper.readTree(mockMvc.perform(get("/users")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString());
        JsonNode user = findUser(listed, userId);
        assertTrue(user.get("enabled").asBoolean());
        assertFalse(user.get("pendingInvitation").asBoolean());
        assertTrue(user.get("invitationExpiresAt").isNull());
    }

    @Test
    void the_clear_token_is_never_stored_or_audited() throws Exception {
        String adminToken = login("admin", "adminpass");
        JsonNode created = objectMapper.readTree(mockMvc.perform(post("/users")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "username", "secret@example.test",
                                "role", "viewer"
                        ))))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString());
        String token = tokenFromSetupLink(created.get("setupLink").asText());

        // Stored as a SHA-256, so a dump of this table cannot take over the account.
        long userId = created.get("id").asLong();
        List<UserInvitation> stored = invitations.findByUserIdAndConsumedAtIsNull(userId);
        assertEquals(1, stored.size());
        String storedHash = stored.get(0).getTokenHash();
        assertNotEquals(token, storedHash);
        assertTrue(storedHash.matches("[0-9a-f]{64}"), "expected a hex SHA-256, got: " + storedHash);

        mockMvc.perform(post("/auth/invite/" + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("password", "brand-new-password-1"))))
                .andExpect(status().isOk());

        // The audit trail records that an invitation happened, never the credential.
        for (AuditEvent event : auditRepository.findAll()) {
            assertFalse(String.valueOf(event.getPath()).contains(token),
                    "audit path leaked the invitation token");
            assertFalse(String.valueOf(event.getUsername()).contains(token),
                    "audit actor leaked the invitation token");
            assertFalse(String.valueOf(event.getAction()).contains(token),
                    "audit action leaked the invitation token");
        }
    }

    @Test
    void an_unknown_token_looks_exactly_like_a_spent_one() throws Exception {
        // Otherwise the endpoint becomes a way to probe which invitations exist.
        String unknown = mockMvc.perform(get("/auth/invite/not-a-real-token"))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();

        String adminToken = login("admin", "adminpass");
        JsonNode created = objectMapper.readTree(mockMvc.perform(post("/users")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "username", "probe@example.test",
                                "role", "viewer"
                        ))))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString());
        String token = tokenFromSetupLink(created.get("setupLink").asText());
        mockMvc.perform(post("/auth/invite/" + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("password", "spent-this-one-01"))))
                .andExpect(status().isOk());

        String spent = mockMvc.perform(get("/auth/invite/" + token))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();

        assertEquals(unknown, spent);
        assertFalse(spent.contains("probe@example.test"));
    }

    @Test
    void an_invited_account_cannot_be_logged_into_before_the_password_is_set() throws Exception {
        String adminToken = login("admin", "adminpass");
        JsonNode created = objectMapper.readTree(mockMvc.perform(post("/users")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "username", "pending@example.test",
                                "role", "viewer"
                        ))))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.enabled").value(false))
                .andReturn().getResponse().getContentAsString());

        // The placeholder password is random and disclosed nowhere, so there is
        // nothing to try; the account is also disabled until the link is used.
        mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "username", "pending@example.test", "password", ""))))
                .andExpect(status().is4xxClientError());

        String token = tokenFromSetupLink(created.get("setupLink").asText());
        mockMvc.perform(post("/auth/invite/" + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("password", "finally-a-password-1"))))
                .andExpect(status().isOk());
        login("pending@example.test", "finally-a-password-1");
    }

    @Test
    void a_short_password_is_refused() throws Exception {
        String adminToken = login("admin", "adminpass");
        JsonNode created = objectMapper.readTree(mockMvc.perform(post("/users")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "username", "weak@example.test",
                                "role", "viewer"
                        ))))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString());
        String token = tokenFromSetupLink(created.get("setupLink").asText());

        mockMvc.perform(post("/auth/invite/" + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("password", "short"))))
                .andExpect(status().isBadRequest());

        // Refusing must not burn the token — the invitee has to be able to retry.
        mockMvc.perform(get("/auth/invite/" + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.valid").value(true));
    }

    private static String tokenFromSetupLink(String setupLink) {
        String path = URI.create(setupLink).getPath();
        return path.substring(path.lastIndexOf('/') + 1);
    }

    private static JsonNode findUser(JsonNode users, long userId) {
        for (JsonNode user : users) {
            if (user.get("id").asLong() == userId) return user;
        }
        throw new AssertionError("user not listed: " + userId);
    }
}
