package com.agora.gateway;

import com.agora.gateway.security.TotpService;
import com.fasterxml.jackson.databind.JsonNode;
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
import org.springframework.transaction.annotation.Transactional;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:mfadb;DB_CLOSE_DELAY=-1",
        "spring.datasource.driver-class-name=org.h2.Driver",
        "spring.datasource.username=sa",
        "spring.datasource.password=",
        "spring.jpa.database-platform=org.hibernate.dialect.H2Dialect",
        "spring.jpa.hibernate.ddl-auto=create-drop",
        "gateway.jwt.secret=test-secret-test-secret-test-secret-0123",
        "gateway.owner.username=owner",
        "gateway.owner.password=ownerpass"
})
@Transactional // each test's MFA enrollment/recovery-code mutations roll back — the H2
                // instance (DB_CLOSE_DELAY=-1) is shared across every method in this class.
class MfaTest {

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
    private TotpService totpService;

    @AfterEach
    void resetWireMock() {
        wireMock.resetAll();
    }

    private JsonNode json(org.springframework.test.web.servlet.MvcResult result) throws Exception {
        return objectMapper.readTree(result.getResponse().getContentAsString());
    }

    private String login(String username, String password) throws Exception {
        String body = objectMapper.writeValueAsString(Map.of("username", username, "password", password));
        var result = mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isOk())
                .andReturn();
        return json(result).get("token").asText();
    }

    /** Enrolls owner into MFA end to end; returns the recovery codes. */
    private java.util.List<String> enroll(String accessToken) throws Exception {
        var setupResult = mockMvc.perform(post("/auth/mfa/setup")
                        .header("Authorization", "Bearer " + accessToken))
                .andExpect(status().isOk())
                .andReturn();
        String secret = json(setupResult).get("secret").asText();

        var confirmBody = objectMapper.writeValueAsString(Map.of("code", currentCode(secret)));
        var confirmResult = mockMvc.perform(post("/auth/mfa/confirm")
                        .header("Authorization", "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(confirmBody))
                .andExpect(status().isOk())
                .andReturn();
        JsonNode codes = json(confirmResult).get("recovery_codes");
        java.util.List<String> out = new java.util.ArrayList<>();
        codes.forEach(n -> out.add(n.asText()));
        return out;
    }

    /** Stands in for the authenticator app: the real TOTP code for `secret` right now. */
    private String currentCode(String secret) {
        return totpService.currentCode(secret);
    }

    @Test
    void setup_then_confirm_enables_mfa() throws Exception {
        String token = login("owner", "ownerpass");

        var recoveryCodes = enroll(token);

        assertEquals(8, recoveryCodes.size());
    }

    @Test
    void confirm_rejects_wrong_code() throws Exception {
        String token = login("owner", "ownerpass");
        mockMvc.perform(post("/auth/mfa/setup").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk());

        String body = objectMapper.writeValueAsString(Map.of("code", "000000"));
        mockMvc.perform(post("/auth/mfa/confirm")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void login_after_enrollment_requires_second_factor() throws Exception {
        String token = login("owner", "ownerpass");
        enroll(token);

        String body = objectMapper.writeValueAsString(Map.of("username", "owner", "password", "ownerpass"));
        mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.mfa_required").value(true))
                .andExpect(jsonPath("$.challenge_token").exists())
                .andExpect(jsonPath("$.token").doesNotExist());
    }

    @Test
    void verify_with_correct_totp_issues_real_token() throws Exception {
        String setupToken = login("owner", "ownerpass");
        var setupResult = mockMvc.perform(post("/auth/mfa/setup")
                        .header("Authorization", "Bearer " + setupToken))
                .andReturn();
        String secret = json(setupResult).get("secret").asText();
        String firstCode = currentCode(secret);
        mockMvc.perform(post("/auth/mfa/confirm")
                        .header("Authorization", "Bearer " + setupToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("code", firstCode))))
                .andExpect(status().isOk());

        String loginBody = objectMapper.writeValueAsString(Map.of("username", "owner", "password", "ownerpass"));
        var challengeResult = mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(loginBody))
                .andReturn();
        String challengeToken = json(challengeResult).get("challenge_token").asText();

        String verifyBody = objectMapper.writeValueAsString(Map.of(
                "challenge_token", challengeToken,
                "code", currentCode(secret)
        ));
        mockMvc.perform(post("/auth/mfa/verify")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(verifyBody))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.token").exists())
                .andExpect(jsonPath("$.mfa_required").doesNotExist());
    }

    @Test
    void verify_with_wrong_totp_is_refused() throws Exception {
        String setupToken = login("owner", "ownerpass");
        enroll(setupToken);

        String loginBody = objectMapper.writeValueAsString(Map.of("username", "owner", "password", "ownerpass"));
        var challengeResult = mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(loginBody))
                .andReturn();
        String challengeToken = json(challengeResult).get("challenge_token").asText();

        String verifyBody = objectMapper.writeValueAsString(Map.of(
                "challenge_token", challengeToken,
                "code", "000000"
        ));
        mockMvc.perform(post("/auth/mfa/verify")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(verifyBody))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void recovery_code_works_once_then_is_refused() throws Exception {
        String setupToken = login("owner", "ownerpass");
        var recoveryCodes = enroll(setupToken);
        String recoveryCode = recoveryCodes.get(0);

        String loginBody = objectMapper.writeValueAsString(Map.of("username", "owner", "password", "ownerpass"));
        String challengeToken1 = json(mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(loginBody)).andReturn())
                .get("challenge_token").asText();

        String verifyBody = objectMapper.writeValueAsString(Map.of(
                "challenge_token", challengeToken1,
                "recovery_code", recoveryCode
        ));
        mockMvc.perform(post("/auth/mfa/verify")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(verifyBody))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.token").exists());

        // Same recovery code, fresh challenge — must be refused the second time.
        String challengeToken2 = json(mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(loginBody)).andReturn())
                .get("challenge_token").asText();
        String replayBody = objectMapper.writeValueAsString(Map.of(
                "challenge_token", challengeToken2,
                "recovery_code", recoveryCode
        ));
        mockMvc.perform(post("/auth/mfa/verify")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(replayBody))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void disable_requires_password_and_turns_mfa_off() throws Exception {
        String setupToken = login("owner", "ownerpass");
        enroll(setupToken);

        // Wrong password refused.
        mockMvc.perform(post("/auth/mfa/disable")
                        .header("Authorization", "Bearer " + setupToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("password", "wrong"))))
                .andExpect(status().isUnauthorized());

        mockMvc.perform(post("/auth/mfa/disable")
                        .header("Authorization", "Bearer " + setupToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of("password", "ownerpass"))))
                .andExpect(status().isNoContent());

        String loginBody = objectMapper.writeValueAsString(Map.of("username", "owner", "password", "ownerpass"));
        mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(loginBody))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.token").exists())
                .andExpect(jsonPath("$.mfa_required").doesNotExist());
    }

    @Test
    void setup_without_auth_is_rejected() throws Exception {
        mockMvc.perform(post("/auth/mfa/setup"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void verify_with_garbage_challenge_token_is_rejected() throws Exception {
        String verifyBody = objectMapper.writeValueAsString(Map.of(
                "challenge_token", "not-a-real-token",
                "code", "123456"
        ));
        mockMvc.perform(post("/auth/mfa/verify")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(verifyBody))
                .andExpect(status().isUnauthorized());
    }
}
