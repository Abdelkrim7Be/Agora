package com.agora.gateway.user;

import com.agora.gateway.audit.AuditService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.*;

import java.security.SecureRandom;
import java.time.Instant;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Admin-only staff directory: create/disable/enable users and assign role + department.
 * Never returns a password hash. Route authorization (ROLE_ADMIN) is enforced in
 * SecurityConfig; this controller assumes the caller already cleared that gate.
 */
@RestController
@RequestMapping("/users")
public class UserController {

    private static final Set<String> VALID_ROLES = Set.of("viewer", "approver", "owner", "admin");

    private final UserRepository users;
    private final PasswordEncoder passwordEncoder;
    private final AuditService auditService;
    private final InvitationService invitations;
    private final InvitationMailer mailer;
    private final SecureRandom random = new SecureRandom();

    public UserController(UserRepository users,
                          PasswordEncoder passwordEncoder,
                          AuditService auditService,
                          InvitationService invitations,
                          InvitationMailer mailer) {
        this.users = users;
        this.passwordEncoder = passwordEncoder;
        this.auditService = auditService;
        this.invitations = invitations;
        this.mailer = mailer;
    }

    public record UserResponse(
            Long id,
            String username,
            String email,
            String role,
            String department,
            boolean enabled,
            boolean pendingInvitation,
            boolean invitationExpired,
            Instant invitationExpiresAt
    ) {
        static UserResponse from(AppUser u, UserInvitation invitation) {
            Instant now = Instant.now();
            boolean pending = invitation != null && invitation.isUsable(now);
            boolean expired = invitation != null && !invitation.isConsumed() && invitation.isExpired(now);
            return new UserResponse(u.getId(), u.getUsername(), u.getEmail(), u.getRole(), u.getDepartment(),
                    u.isEnabled(), pending, expired, invitation != null ? invitation.getExpiresAt() : null);
        }
    }

    /**
     * `password` is optional. Omitting it invites the user instead: the account
     * is created with an unguessable placeholder that nothing can log in with,
     * and the invitee sets their own password through the emailed link.
     */
    public record CreateUserRequest(
            @NotBlank String username,
            String password,
            String email,
            @NotBlank String role,
            String department
    ) {}

    public record UpdateUserRequest(@NotBlank String role, String department) {}

    @GetMapping
    public List<UserResponse> list() {
        return users.findAll().stream().map(user -> UserResponse.from(user, currentInvitation(user))).toList();
    }

    @PostMapping
    public ResponseEntity<?> create(@Valid @RequestBody CreateUserRequest req, Authentication auth) {
        String role = req.role().toLowerCase();
        if (!VALID_ROLES.contains(role)) {
            return ResponseEntity.badRequest().body(java.util.Map.of("error", "invalid role: " + req.role()));
        }
        if (users.findByUsername(req.username()).isPresent()) {
            return ResponseEntity.status(409).body(java.util.Map.of("error", "username already exists"));
        }
        boolean invite = req.password() == null || req.password().isBlank();
        // An invited account must not be loggable-into before its password is
        // set, so the placeholder is random and never disclosed anywhere.
        String initialPassword = invite ? randomPlaceholder() : req.password();

        AppUser user = new AppUser(
                req.username(), passwordEncoder.encode(initialPassword), role, req.department());
        user.setEmail(resolveEmail(req.email(), req.username()));
        if (invite) {
            user.setEnabled(false);
        }
        AppUser saved = users.save(user);
        audit(auth, "create_user", "/users", "success");

        Map<String, Object> body = userBody(saved);
        if (invite) {
            body.putAll(issueInvitation(saved, auth));
        }
        return ResponseEntity.status(HttpStatus.CREATED).body(body);
    }

    /** Re-issue an invitation, retiring whatever link was outstanding. */
    @PostMapping("/{id}/invite")
    public ResponseEntity<?> invite(@PathVariable Long id, Authentication auth) {
        return users.findById(id).map(user -> {
            Map<String, Object> body = userBody(user);
            body.putAll(issueInvitation(user, auth));
            return ResponseEntity.ok(body);
        }).orElseGet(() -> ResponseEntity.notFound().build());
    }

    /** The user fields, flat, so invitation metadata can be added alongside
     * without changing the shape existing callers already read. */
    private static Map<String, Object> userBody(AppUser user) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("id", user.getId());
        body.put("username", user.getUsername());
        body.put("email", user.getEmail());
        body.put("role", user.getRole());
        body.put("department", user.getDepartment());
        body.put("enabled", user.isEnabled());
        return body;
    }

    private Map<String, Object> issueInvitation(AppUser user, Authentication auth) {
        InvitationService.Issued issued = invitations.create(
                user, auth != null ? auth.getName() : null);
        boolean sent = mailer.send(user.getEmail(), user.getUsername(), issued.setupLink());
        audit(auth, "invite_user", "/users/" + user.getId(), sent ? "success" : "pending_delivery");

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("invitationSent", sent);
        body.put("invitationExpiresAt", issued.expiresAt());
        // With no relay — or a relay that just refused the message — the admin
        // has to be able to hand the link over themselves, or the account is
        // stranded with a password nobody knows.
        if (!sent) {
            body.put("setupLink", issued.setupLink());
        }
        return body;
    }

    private String randomPlaceholder() {
        byte[] raw = new byte[32];
        random.nextBytes(raw);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
    }

    private static String resolveEmail(String email, String username) {
        if (email != null && !email.isBlank()) return email.strip();
        // Usernames are addresses in every deployment so far; fall back to that
        // rather than creating an account with nowhere to send the invitation.
        return username != null && username.contains("@") ? username.strip() : null;
    }

    @PutMapping("/{id}")
    public ResponseEntity<?> update(@PathVariable Long id, @Valid @RequestBody UpdateUserRequest req, Authentication auth) {
        String role = req.role().toLowerCase();
        if (!VALID_ROLES.contains(role)) {
            return ResponseEntity.badRequest().body(java.util.Map.of("error", "invalid role: " + req.role()));
        }
        return users.findById(id).map(u -> {
            u.setRole(role);
            u.setDepartment(req.department());
            users.save(u);
            audit(auth, "update_user", "/users/" + id, "success");
            return ResponseEntity.ok(UserResponse.from(u, currentInvitation(u)));
        }).orElseGet(() -> ResponseEntity.notFound().build());
    }

    @PostMapping("/{id}/disable")
    public ResponseEntity<?> disable(@PathVariable Long id, Authentication auth) {
        return setEnabled(id, false, auth, "disable_user");
    }

    @PostMapping("/{id}/enable")
    public ResponseEntity<?> enable(@PathVariable Long id, Authentication auth) {
        return setEnabled(id, true, auth, "enable_user");
    }

    private ResponseEntity<?> setEnabled(Long id, boolean enabled, Authentication auth, String action) {
        return users.findById(id).map(u -> {
            if (!enabled && auth != null && u.getUsername().equals(auth.getName())) {
                audit(auth, action, "/users/" + id, "denied");
                return ResponseEntity.badRequest().body(java.util.Map.of("error", "cannot disable current user"));
            }
            u.setEnabled(enabled);
            users.save(u);
            audit(auth, action, "/users/" + id, "success");
            return ResponseEntity.ok(UserResponse.from(u, currentInvitation(u)));
        }).orElseGet(() -> ResponseEntity.notFound().build());
    }

    private UserInvitation currentInvitation(AppUser user) {
        return invitations.currentOpenInvitation(user).orElse(null);
    }

    private void audit(Authentication auth, String action, String path, String outcome) {
        String actor = auth != null ? auth.getName() : null;
        String actorRole = auth != null
                ? auth.getAuthorities().stream().findFirst().map(Object::toString)
                        .map(a -> a.startsWith("ROLE_") ? a.substring(5).toLowerCase() : a)
                        .orElse(null)
                : null;
        auditService.record(actor, actorRole, action, "POST", path, null, outcome);
    }
}
