package com.agora.gateway;

import com.agora.gateway.security.JwtService;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.jsonwebtoken.Claims;
import jakarta.servlet.http.Cookie;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import java.time.Duration;
import java.time.Instant;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:jwthardeningdb;DB_CLOSE_DELAY=-1",
        "spring.datasource.driver-class-name=org.h2.Driver",
        "spring.datasource.username=sa",
        "spring.datasource.password=",
        "spring.jpa.database-platform=org.hibernate.dialect.H2Dialect",
        "spring.jpa.hibernate.ddl-auto=create-drop",
        "gateway.jwt.secret=test-secret-test-secret-test-secret-0123",
        "gateway.jwt.ttl-minutes=15",
        "gateway.jwt.refresh-ttl-days=7",
        "gateway.jwt.secure-cookies=false",
        "gateway.owner.username=owner",
        "gateway.owner.password=ownerpass"
})
class JwtHardeningTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private JwtService jwtService;

    @Test
    void refreshRotationReplayDetectionAndLogoutRevokeTokenFamilies() throws Exception {
        MvcResult login = login();
        JsonNode loginBody = json(login);
        String firstAccess = loginBody.get("token").asText();
        Cookie firstRefresh = login.getResponse().getCookie("agora_refresh");

        assertThat(loginBody.get("access_token").asText()).isEqualTo(firstAccess);
        assertThat(loginBody.get("token_type").asText()).isEqualTo("Bearer");
        assertThat(loginBody.get("expires_in").asLong()).isEqualTo(900);
        assertThat(loginBody.get("refresh_expires_in").asLong()).isEqualTo(604_800);
        assertThat(firstRefresh).isNotNull();
        assertThat(firstRefresh.isHttpOnly()).isTrue();

        Claims claims = jwtService.parse(firstAccess);
        assertThat(claims.getId()).isNotBlank();
        assertThat(claims.getIssuer()).isEqualTo("agora-gateway");
        assertThat(claims.get("type", String.class)).isEqualTo("access");
        long lifetimeSeconds = Duration.between(
                claims.getIssuedAt().toInstant(),
                claims.getExpiration().toInstant()
        ).toSeconds();
        assertThat(lifetimeSeconds).isEqualTo(900);
        assertThat(claims.getExpiration().toInstant()).isAfter(Instant.now());

        MvcResult rotation = mockMvc.perform(post("/auth/refresh").cookie(firstRefresh))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.token").isNotEmpty())
                .andReturn();
        Cookie secondRefresh = rotation.getResponse().getCookie("agora_refresh");
        assertThat(secondRefresh).isNotNull();
        assertThat(secondRefresh.getValue()).isNotEqualTo(firstRefresh.getValue());

        mockMvc.perform(post("/auth/refresh").cookie(firstRefresh))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("invalid refresh token"));

        mockMvc.perform(post("/auth/refresh").cookie(secondRefresh))
                .andExpect(status().isUnauthorized());

        MvcResult secondLogin = login();
        String logoutAccess = json(secondLogin).get("token").asText();
        Cookie logoutRefresh = secondLogin.getResponse().getCookie("agora_refresh");

        mockMvc.perform(get("/agents")
                        .header("Authorization", "Bearer " + logoutAccess))
                .andExpect(status().isOk());

        mockMvc.perform(post("/auth/logout")
                        .header("Authorization", "Bearer " + logoutAccess)
                        .cookie(logoutRefresh))
                .andExpect(status().isNoContent());

        mockMvc.perform(get("/agents")
                        .header("Authorization", "Bearer " + logoutAccess))
                .andExpect(status().isUnauthorized());

        mockMvc.perform(post("/auth/refresh").cookie(logoutRefresh))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void actuator_prometheus_is_scrapeable_without_jwt() throws Exception {
        mockMvc.perform(get("/actuator/prometheus"))
                .andExpect(status().isOk());
    }

    private MvcResult login() throws Exception {
        String body = objectMapper.writeValueAsString(Map.of(
                "username", "owner",
                "password", "ownerpass"
        ));
        return mockMvc.perform(post("/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isOk())
                .andReturn();
    }

    private JsonNode json(MvcResult result) throws Exception {
        return objectMapper.readTree(result.getResponse().getContentAsString());
    }
}
