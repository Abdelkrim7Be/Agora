package com.agora.gateway.user;

import com.agora.gateway.config.GatewayProperties;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.Optional;

/**
 * Issues and redeems "set your password" invitations.
 *
 * The clear token is returned exactly once, from {@link #create}. Everything
 * afterwards works from its SHA-256, so the token cannot be recovered from
 * storage, from logs, or from the audit trail.
 */
@Service
public class InvitationService {

    /** 32 bytes of CSPRNG output, base64url — well past guessing range. */
    private static final int TOKEN_BYTES = 32;

    private final UserRepository users;
    private final UserInvitationRepository invitations;
    private final PasswordEncoder passwordEncoder;
    private final GatewayProperties properties;
    private final SecureRandom random = new SecureRandom();

    public InvitationService(UserRepository users,
                             UserInvitationRepository invitations,
                             PasswordEncoder passwordEncoder,
                             GatewayProperties properties) {
        this.users = users;
        this.invitations = invitations;
        this.passwordEncoder = passwordEncoder;
        this.properties = properties;
    }

    /** The clear token and where it can be redeemed. Held only in memory. */
    public record Issued(String token, String setupLink, Instant expiresAt) {}

    /** What a public caller is allowed to learn about a token. */
    public record Status(boolean valid, String username, Instant expiresAt) {
        static Status invalid() { return new Status(false, null, null); }
    }

    static String hash(String token) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] out = digest.digest(token.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder(out.length * 2);
            for (byte b : out) hex.append(String.format("%02x", b));
            return hex.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }

    @Transactional
    public Issued create(AppUser user, String createdBy) {
        // Supersede anything outstanding: two live tokens for one account means a
        // resend does not actually retire the link that may have gone astray.
        for (UserInvitation previous : invitations.findByUserIdAndConsumedAtIsNull(user.getId())) {
            previous.setConsumedAt(Instant.now());
            invitations.save(previous);
        }

        byte[] raw = new byte[TOKEN_BYTES];
        random.nextBytes(raw);
        String token = Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
        Instant expiresAt = Instant.now().plus(properties.getInviteExpiryHours(), ChronoUnit.HOURS);

        invitations.save(new UserInvitation(user.getId(), hash(token), expiresAt, createdBy));
        return new Issued(token, buildSetupLink(token), expiresAt);
    }

    public String buildSetupLink(String token) {
        String base = properties.getAppUrl() == null ? "" : properties.getAppUrl().replaceAll("/+$", "");
        return base + "/invite/" + token;
    }

    /**
     * Public token lookup.
     *
     * Unknown, expired and already-consumed tokens all return the same
     * {@link Status#invalid()} — the endpoint must not become a way to probe
     * which accounts exist or which invitations are outstanding.
     */
    public Status status(String token) {
        return usable(token)
                .flatMap(invitation -> users.findById(invitation.getUserId())
                        .map(user -> new Status(true, user.getUsername(), invitation.getExpiresAt())))
                .orElseGet(Status::invalid);
    }

    public Optional<UserInvitation> currentOpenInvitation(AppUser user) {
        if (user == null || user.getId() == null) return Optional.empty();
        return invitations.findByUserIdAndConsumedAtIsNull(user.getId()).stream().findFirst();
    }

    /**
     * Set the password and burn the token, or return false if the token is not usable.
     *
     * Both writes happen in one transaction: a password set with a still-live
     * token would let the link be replayed.
     */
    @Transactional
    public boolean consume(String token, String password) {
        Optional<UserInvitation> found = usable(token);
        if (found.isEmpty()) return false;
        UserInvitation invitation = found.get();

        Optional<AppUser> user = users.findById(invitation.getUserId());
        if (user.isEmpty()) return false;

        AppUser target = user.get();
        target.setPasswordHash(passwordEncoder.encode(password));
        // An invited account is usable the moment its password is set; leaving it
        // disabled would make the invitation link look broken.
        target.setEnabled(true);
        users.save(target);

        invitation.setConsumedAt(Instant.now());
        invitations.save(invitation);
        return true;
    }

    private Optional<UserInvitation> usable(String token) {
        if (token == null || token.isBlank()) return Optional.empty();
        return invitations.findByTokenHash(hash(token))
                .filter(invitation -> invitation.isUsable(Instant.now()));
    }
}
