package com.agora.gateway.security;

import com.agora.gateway.config.GatewayProperties;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import org.springframework.stereotype.Service;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.util.Date;
import java.util.UUID;

@Service
public class JwtService {

    private static final String ISSUER = "agora-gateway";

    private final SecretKey key;
    private final long ttlMillis;
    private final long mfaChallengeTtlMillis;

    public JwtService(GatewayProperties props) {
        String secret = props.getJwt().getSecret();
        if (secret == null || secret.isBlank()) {
            throw new IllegalStateException("GATEWAY_JWT_SECRET must be set");
        }
        byte[] bytes = secret.getBytes(StandardCharsets.UTF_8);
        if (bytes.length < 32) {
            throw new IllegalStateException("GATEWAY_JWT_SECRET must be at least 32 bytes for HS256");
        }
        this.key = Keys.hmacShaKeyFor(bytes);
        this.ttlMillis = props.getJwt().getTtlMinutes() * 60_000L;
        if (ttlMillis <= 0) {
            throw new IllegalStateException("GATEWAY_JWT_TTL_MINUTES must be positive");
        }
        this.mfaChallengeTtlMillis = props.getMfa().getChallengeTtlMinutes() * 60_000L;
        if (mfaChallengeTtlMillis <= 0) {
            throw new IllegalStateException("GATEWAY_MFA_CHALLENGE_TTL_MINUTES must be positive");
        }
    }

    public String generate(String username, String role) {
        Date now = new Date();
        return Jwts.builder()
                .id(UUID.randomUUID().toString())
                .issuer(ISSUER)
                .subject(username)
                .claim("role", role)
                .claim("type", "access")
                .issuedAt(now)
                .expiration(new Date(now.getTime() + ttlMillis))
                .signWith(key)
                .compact();
    }

    public Claims parse(String token) {
        return Jwts.parser()
                .verifyWith(key)
                .requireIssuer(ISSUER)
                .require("type", "access")
                .build()
                .parseSignedClaims(token)
                .getPayload();
    }

    /**
     * Short-lived, role-less token proving "password already checked, TOTP still
     * owed". Its distinct "type" claim means JwtAuthFilter's parse() (which
     * requires type=access) rejects it outright — it authenticates nothing except
     * a call to /auth/mfa/verify.
     */
    public String generateMfaChallenge(String username) {
        Date now = new Date();
        return Jwts.builder()
                .id(UUID.randomUUID().toString())
                .issuer(ISSUER)
                .subject(username)
                .claim("type", "mfa_challenge")
                .issuedAt(now)
                .expiration(new Date(now.getTime() + mfaChallengeTtlMillis))
                .signWith(key)
                .compact();
    }

    public Claims parseMfaChallenge(String token) {
        return Jwts.parser()
                .verifyWith(key)
                .requireIssuer(ISSUER)
                .require("type", "mfa_challenge")
                .build()
                .parseSignedClaims(token)
                .getPayload();
    }

    public long getTtlSeconds() {
        return ttlMillis / 1_000L;
    }
}
