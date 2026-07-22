package com.agora.gateway;

import com.agora.gateway.audit.AuditRepository;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:loginratelimitdb;DB_CLOSE_DELAY=-1",
        "spring.datasource.driver-class-name=org.h2.Driver",
        "spring.datasource.username=sa",
        "spring.datasource.password=",
        "spring.jpa.database-platform=org.hibernate.dialect.H2Dialect",
        "spring.jpa.hibernate.ddl-auto=create-drop",
        "gateway.jwt.secret=test-secret-test-secret-test-secret-0123",
        "gateway.owner.username=owner",
        "gateway.owner.password=ownerpass",
        "gateway.login-rate-limit.enabled=true",
        "gateway.login-rate-limit.window-seconds=120",
        "gateway.login-rate-limit.max-failures=2",
        "gateway.login-rate-limit.global-max-failures=4"
})
class LoginRateLimitTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private AuditRepository auditRepository;

    @Test
    void login_failures_trigger_account_and_global_limits() throws Exception {
        login("owner", "wrong-one", "203.0.113.10")
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("invalid credentials"));

        login("owner", "wrong-two", "198.51.100.20")
                .andExpect(status().isTooManyRequests())
                .andExpect(header().string("Retry-After", "120"))
                .andExpect(jsonPath("$.error").value("too many login attempts"))
                .andExpect(jsonPath("$.retry_after_seconds").value(120));

        // Changing a client-controlled forwarding header cannot bypass the account bucket.
        login("owner", "ownerpass", "192.0.2.30")
                .andExpect(status().isTooManyRequests());

        login("unknown-a", "wrong", "192.0.2.40")
                .andExpect(status().isUnauthorized());

        // The fourth failure in the window activates the platform-wide flood limit.
        login("unknown-b", "wrong", "192.0.2.50")
                .andExpect(status().isTooManyRequests());

        login("unknown-c", "wrong", "192.0.2.60")
                .andExpect(status().isTooManyRequests());

        long rateLimitedAudits = auditRepository.findAll().stream()
                .filter(event -> "login".equals(event.getAction()))
                .filter(event -> "rate_limited".equals(event.getOutcome()))
                .count();
        assertTrue(rateLimitedAudits >= 4);

        String oversizedUsername = "x".repeat(129);
        mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "username", oversizedUsername,
                                "password", "password"
                        ))))
                .andExpect(status().isBadRequest());
    }

    private org.springframework.test.web.servlet.ResultActions login(
            String username,
            String password,
            String forwardedFor
    ) throws Exception {
        return mockMvc.perform(post("/auth/login")
                .header("X-Forwarded-For", forwardedFor)
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(Map.of(
                        "username", username,
                        "password", password
                ))));
    }
}
