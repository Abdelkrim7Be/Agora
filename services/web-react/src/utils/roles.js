export const ROLE_RANK = { viewer: 1, approver: 2, owner: 3, admin: 4 };

export function roleAtLeast(role, minimum) {
  return (ROLE_RANK[role] || 0) >= (ROLE_RANK[minimum] || 99);
}
