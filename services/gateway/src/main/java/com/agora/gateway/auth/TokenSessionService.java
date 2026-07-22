package com.agora.gateway.auth;

import com.agora.gateway.config.GatewayProperties;
import com.agora.gateway.security.JwtService;
import com.agora.gateway.user.AppUser;
import com.agora.gateway.user.UserRepository;
import io.jsonwebtoken.Claims;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Instant;
import java.util.Base64;
import java.util.Optional;
import java.util.UUID;

@Service
public class TokenSessionService {

    public enum RotationStatus { SUCCESS, INVALID, REPLAYED }

    public record TokenPair(String accessToken, String refreshToken) {}
    public record RotationResult(RotationStatus status, TokenPair tokens) {}

    private final RefreshTokenRepository refreshTokens;
    private final RevokedAccessTokenRepository revokedAccessTokens;
    private final UserRepository users;
    private final JwtService jwtService;
    private final SecureRandom secureRandom = new SecureRandom();
    private final long refreshTtlSeconds;

    public TokenSessionService(
            RefreshTokenRepository refreshTokens,
            RevokedAccessTokenRepository revokedAccessTokens,
            UserRepository users,
            JwtService jwtService,
            GatewayProperties properties
    ) {
        this.refreshTokens = refreshTokens;
        this.revokedAccessTokens = revokedAccessTokens;
        this.users = users;
        this.jwtService = jwtService;
        this.refreshTtlSeconds = properties.getJwt().getRefreshTtlDays() * 86_400L;
        if (refreshTtlSeconds <= 0) {
            throw new IllegalStateException("GATEWAY_JWT_REFRESH_TTL_DAYS must be positive");
        }
    }

    @Transactional
    public TokenPair issue(String username, String role) {
        Instant now = Instant.now();
        refreshTokens.deleteByExpiresAtBefore(now);
        revokedAccessTokens.deleteByExpiresAtBefore(now);
        return issue(username, role, UUID.randomUUID().toString(), now);
    }

    @Transactional
    public RotationResult rotate(String rawToken) {
        if (rawToken == null || rawToken.isBlank()) {
            return new RotationResult(RotationStatus.INVALID, null);
        }

        Instant now = Instant.now();
        Optional<RefreshToken> stored = refreshTokens.lockByTokenHash(hash(rawToken));
        if (stored.isEmpty()) {
            return new RotationResult(RotationStatus.INVALID, null);
        }

        RefreshToken current = stored.get();
        if (current.getUsedAt() != null) {
            revokeFamily(current.getFamilyId(), now);
            return new RotationResult(RotationStatus.REPLAYED, null);
        }
        if (current.getRevokedAt() != null || !current.getExpiresAt().isAfter(now)) {
            return new RotationResult(RotationStatus.INVALID, null);
        }

        Optional<AppUser> user = users.findByUsername(current.getUsername());
        if (user.isEmpty() || !user.get().isEnabled()) {
            revokeFamily(current.getFamilyId(), now);
            return new RotationResult(RotationStatus.INVALID, null);
        }

        TokenPair replacement = issue(
                user.get().getUsername(),
                user.get().getRole(),
                current.getFamilyId(),
                now
        );
        current.markUsed(now, hash(replacement.refreshToken()));
        refreshTokens.save(current);
        return new RotationResult(RotationStatus.SUCCESS, replacement);
    }

    @Transactional
    public void logout(Claims accessClaims, String rawRefreshToken) {
        String tokenId = accessClaims.getId();
        if (tokenId != null && accessClaims.getExpiration() != null
                && accessClaims.getExpiration().toInstant().isAfter(Instant.now())) {
            revokedAccessTokens.save(new RevokedAccessToken(
                    tokenId,
                    accessClaims.getExpiration().toInstant()
            ));
        }

        if (rawRefreshToken == null || rawRefreshToken.isBlank()) {
            return;
        }
        refreshTokens.lockByTokenHash(hash(rawRefreshToken))
                .filter(token -> token.getUsername().equals(accessClaims.getSubject()))
                .ifPresent(token -> revokeFamily(token.getFamilyId(), Instant.now()));
    }

    public boolean isAccessRevoked(String tokenId) {
        return tokenId == null || revokedAccessTokens.existsById(tokenId);
    }

    public long getRefreshTtlSeconds() {
        return refreshTtlSeconds;
    }

    private TokenPair issue(String username, String role, String familyId, Instant now) {
        byte[] randomBytes = new byte[32];
        secureRandom.nextBytes(randomBytes);
        String rawRefreshToken = Base64.getUrlEncoder().withoutPadding().encodeToString(randomBytes);
        refreshTokens.save(new RefreshToken(
                hash(rawRefreshToken),
                familyId,
                username,
                now,
                now.plusSeconds(refreshTtlSeconds)
        ));
        return new TokenPair(jwtService.generate(username, role), rawRefreshToken);
    }

    private void revokeFamily(String familyId, Instant now) {
        for (RefreshToken token : refreshTokens.findAllByFamilyId(familyId)) {
            token.revoke(now);
            refreshTokens.save(token);
        }
    }

    private static String hash(String token) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(token.getBytes(StandardCharsets.UTF_8));
            return java.util.HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 unavailable", impossible);
        }
    }
}
