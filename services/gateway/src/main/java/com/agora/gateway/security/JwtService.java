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

    public long getTtlSeconds() {
        return ttlMillis / 1_000L;
    }
}
