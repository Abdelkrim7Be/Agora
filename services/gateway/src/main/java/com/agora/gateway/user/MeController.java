package com.agora.gateway.user;

import com.agora.gateway.audit.AuditService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.*;

import java.util.Map;
import java.util.Optional;

/**
 * Every account's own profile.
 *
 * Separate from {@link UserController}, which is the admin's staff directory and
 * is gated on ROLE_ADMIN: an ordinary viewer must be able to correct their own
 * display name or rotate their own password without being able to read, let
 * alone edit, anyone else's. The account is always resolved from the
 * authenticated principal — never from a path or body parameter — so there is no
 * id a caller could swap to reach another account.
 */
@RestController
@RequestMapping("/me")
public class MeController {

    private final UserRepository users;
    private final PasswordEncoder passwordEncoder;
    private final AuditService auditService;

    public MeController(UserRepository users, PasswordEncoder passwordEncoder, AuditService auditService) {
        this.users = users;
        this.passwordEncoder = passwordEncoder;
        this.auditService = auditService;
    }

    public record ProfileResponse(Long id, String username, String email, String role,
                                  String department, String displayName, boolean mfaEnabled) {
        static ProfileResponse from(AppUser user) {
            return new ProfileResponse(
                    user.getId(), user.getUsername(), user.getEmail(),
                    user.getRole(), user.getDepartment(), user.getDisplayName(), user.isMfaEnabled()
            );
        }
    }

    public record UpdateProfileRequest(String email, String displayName) {}

    /** Rotating your own password requires proving you hold the current one. */
    public record ChangePasswordRequest(@NotBlank String currentPassword, @NotBlank String newPassword) {}

    @GetMapping
    public ResponseEntity<?> me(Authentication auth) {
        return currentUser(auth)
                .<ResponseEntity<?>>map(user -> ResponseEntity.ok(ProfileResponse.from(user)))
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    @PutMapping
    public ResponseEntity<?> updateProfile(@Valid @RequestBody UpdateProfileRequest req, Authentication auth) {
        return currentUser(auth).<ResponseEntity<?>>map(user -> {
            if (req.email() != null) {
                user.setEmail(req.email().isBlank() ? null : req.email().strip());
            }
            if (req.displayName() != null) {
                user.setDisplayName(req.displayName().isBlank() ? null : req.displayName().strip());
            }
            // Deliberately not settable here: role, department and enabled are the
            // administrator's to decide, and this endpoint is reachable by everyone.
            users.save(user);
            auditService.record(user.getUsername(), user.getRole(), "update_own_profile",
                    "PUT", "/me", null, "success");
            return ResponseEntity.ok(ProfileResponse.from(user));
        }).orElseGet(() -> ResponseEntity.notFound().build());
    }

    @PutMapping("/password")
    public ResponseEntity<?> changePassword(@Valid @RequestBody ChangePasswordRequest req, Authentication auth) {
        Optional<AppUser> found = currentUser(auth);
        if (found.isEmpty()) return ResponseEntity.notFound().build();
        AppUser user = found.get();

        if (!passwordEncoder.matches(req.currentPassword(), user.getPasswordHash())) {
            auditService.record(user.getUsername(), user.getRole(), "change_own_password",
                    "PUT", "/me/password", null, "wrong_current_password");
            return ResponseEntity.badRequest().body(Map.of("error", "current password is incorrect"));
        }
        String rejection = UserController.rejectWeakPassword(req.newPassword());
        if (rejection != null) {
            return ResponseEntity.badRequest().body(Map.of("error", rejection));
        }

        user.setPasswordHash(passwordEncoder.encode(req.newPassword()));
        users.save(user);
        auditService.record(user.getUsername(), user.getRole(), "change_own_password",
                "PUT", "/me/password", null, "success");
        return ResponseEntity.ok(Map.of("status", "updated"));
    }

    private Optional<AppUser> currentUser(Authentication auth) {
        if (auth == null || auth.getName() == null) return Optional.empty();
        return users.findByUsername(auth.getName());
    }
}
