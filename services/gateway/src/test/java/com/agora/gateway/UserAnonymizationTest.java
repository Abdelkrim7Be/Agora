package com.agora.gateway;

import com.agora.gateway.agent.AgentInstanceGrantRepository;
import com.agora.gateway.audit.AuditRepository;
import com.agora.gateway.user.UserRepository;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:anondb;DB_CLOSE_DELAY=-1",
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
class UserAnonymizationTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private UserRepository users;

    @Autowired
    private AuditRepository auditRepository;

    @Autowired
    private AgentInstanceGrantRepository grants;

    private String login(String username, String password) throws Exception {
        String body = objectMapper.writeValueAsString(Map.of("username", username, "password", password));
        String response = mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        return objectMapper.readTree(response).get("token").asText();
    }

    /** Create a real staff account with a known password, and return its id. */
    private long createUser(String adminToken, String username, String password) throws Exception {
        String body = objectMapper.writeValueAsString(Map.of(
                "username", username,
                "password", password,
                "email", username + "@example.com",
                "role", "viewer"
        ));
        String response = mockMvc.perform(post("/users")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        return objectMapper.readTree(response).get("id").asLong();
    }

    private JsonNode anonymize(String adminToken, long id) throws Exception {
        String response = mockMvc.perform(post("/users/" + id + "/anonymize")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        return objectMapper.readTree(response);
    }

    @Test
    void an_anonymized_account_keeps_its_id_and_loses_every_identifying_field() throws Exception {
        String adminToken = login("admin", "adminpass");
        long id = createUser(adminToken, "departing", "leavingpass");

        anonymize(adminToken, id);

        var user = users.findById(id).orElseThrow();
        assertThat(user.getUsername()).isEqualTo("deleted-user-" + id);
        assertThat(user.getEmail()).isNull();
        assertThat(user.getDisplayName()).isNull();
        assertThat(user.getDepartment()).isNull();
        assertThat(user.isEnabled()).isFalse();
        assertThat(user.isMfaEnabled()).isFalse();
        assertThat(user.getMfaSecret()).isNull();
    }

    @Test
    void an_anonymized_account_cannot_log_in_with_its_old_password() throws Exception {
        String adminToken = login("admin", "adminpass");
        long id = createUser(adminToken, "gone-soon", "leavingpass");

        anonymize(adminToken, id);

        mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(
                                Map.of("username", "gone-soon", "password", "leavingpass"))))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void the_old_name_is_gone_from_the_audit_trail() throws Exception {
        String adminToken = login("admin", "adminpass");
        long id = createUser(adminToken, "traceable", "traceablepass");
        // Produce audit rows under the real name before erasing it.
        login("traceable", "traceablepass");

        JsonNode result = anonymize(adminToken, id);

        assertThat(result.get("auditEventsRenamed").asInt()).isGreaterThan(0);
        assertThat(auditRepository.findAll())
                .noneMatch(event -> "traceable".equalsIgnoreCase(event.getUsername()));
        assertThat(auditRepository.findAll())
                .anyMatch(event -> ("deleted-user-" + id).equals(event.getUsername()));
    }

    @Test
    void the_hash_chain_still_verifies_after_the_trail_is_rewritten() throws Exception {
        String adminToken = login("admin", "adminpass");
        long id = createUser(adminToken, "resealed", "resealedpass");
        login("resealed", "resealedpass");

        anonymize(adminToken, id);

        // The rewrite invalidates every link after it; resealing is what keeps
        // /audit/verify honest instead of permanently reporting tampering.
        mockMvc.perform(get("/audit/verify").header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.valid").value(true))
                .andExpect(jsonPath("$.brokenAtId").doesNotExist());
    }

    @Test
    void the_erasure_is_itself_recorded() throws Exception {
        String adminToken = login("admin", "adminpass");
        long id = createUser(adminToken, "audited-erasure", "somepass");

        anonymize(adminToken, id);

        assertThat(auditRepository.findAll())
                .anyMatch(event -> "anonymize_user".equals(event.getAction())
                        && ("deleted-user-" + id).equals(event.getUsername()));
    }

    @Test
    void mailbox_access_is_revoked() throws Exception {
        String adminToken = login("admin", "adminpass");
        String ownerToken = login("owner", "ownerpass");
        long id = createUser(adminToken, "delegate", "delegatepass");
        mockMvc.perform(post("/agent-instances/default-email-agent/grants")
                        .header("Authorization", "Bearer " + ownerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(
                                Map.of("user_id", "delegate", "role", "viewer"))))
                .andExpect(status().isCreated());
        assertThat(grants.findByUserIdIgnoreCase("delegate")).isNotEmpty();

        JsonNode result = anonymize(adminToken, id);

        assertThat(result.get("grantsRevoked").asInt()).isEqualTo(1);
        assertThat(grants.findByUserIdIgnoreCase("delegate")).isEmpty();
    }

    @Test
    void an_admin_cannot_erase_their_own_account() throws Exception {
        String adminToken = login("admin", "adminpass");
        long adminId = users.findByUsername("admin").orElseThrow().getId();

        mockMvc.perform(post("/users/" + adminId + "/anonymize")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("cannot anonymize current user"));
    }

    @Test
    void anonymizing_twice_is_refused_rather_than_renaming_again() throws Exception {
        String adminToken = login("admin", "adminpass");
        long id = createUser(adminToken, "twice", "twicepass");
        anonymize(adminToken, id);

        mockMvc.perform(post("/users/" + id + "/anonymize")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("user is already anonymized"));
    }

    @Test
    void a_non_admin_cannot_erase_anyone() throws Exception {
        String adminToken = login("admin", "adminpass");
        String ownerToken = login("owner", "ownerpass");
        long id = createUser(adminToken, "protected", "protectedpass");

        mockMvc.perform(post("/users/" + id + "/anonymize")
                        .header("Authorization", "Bearer " + ownerToken))
                .andExpect(status().isForbidden());

        assertThat(users.findById(id).orElseThrow().getUsername()).isEqualTo("protected");
    }

    @Test
    void erasing_an_unknown_account_is_a_404() throws Exception {
        String adminToken = login("admin", "adminpass");

        mockMvc.perform(post("/users/999999/anonymize")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isNotFound());
    }

    @Test
    void the_response_says_agent_side_data_is_erased_separately() throws Exception {
        String adminToken = login("admin", "adminpass");
        long id = createUser(adminToken, "cross-service", "somepass");

        assertThat(anonymize(adminToken, id).get("note").asText()).contains("/gdpr/erase");
    }
}
