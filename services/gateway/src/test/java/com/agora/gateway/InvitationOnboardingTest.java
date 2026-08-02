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

import java.net.URI;
import java.util.Map;

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

    private static String tokenFromSetupLink(String setupLink) {
        String path = URI.create(setupLink).getPath();
        return path.substring(path.lastIndexOf('/') + 1);
    }
}
