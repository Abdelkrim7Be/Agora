package com.agora.gateway.user;

import com.agora.gateway.audit.AuditService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.*;

import java.util.List;
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

    public UserController(UserRepository users, PasswordEncoder passwordEncoder, AuditService auditService) {
        this.users = users;
        this.passwordEncoder = passwordEncoder;
        this.auditService = auditService;
    }

    public record UserResponse(Long id, String username, String role, String department, boolean enabled) {
        static UserResponse from(AppUser u) {
            return new UserResponse(u.getId(), u.getUsername(), u.getRole(), u.getDepartment(), u.isEnabled());
        }
    }

    public record CreateUserRequest(
            @NotBlank String username,
            @NotBlank String password,
            @NotBlank String role,
            String department
    ) {}

    public record UpdateUserRequest(@NotBlank String role, String department) {}

    @GetMapping
    public List<UserResponse> list() {
        return users.findAll().stream().map(UserResponse::from).toList();
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
        AppUser saved = users.save(new AppUser(
                req.username(), passwordEncoder.encode(req.password()), role, req.department()));
        audit(auth, "create_user", "/users", "success");
        return ResponseEntity.status(HttpStatus.CREATED).body(UserResponse.from(saved));
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
            return ResponseEntity.ok(UserResponse.from(u));
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
            return ResponseEntity.ok(UserResponse.from(u));
        }).orElseGet(() -> ResponseEntity.notFound().build());
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
