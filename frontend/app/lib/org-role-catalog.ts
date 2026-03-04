export type OrgRoleMeta = {
  id: string;
  label: string;
  department: string;
  rank: number;
};

const ROLE_CATALOG: Record<string, OrgRoleMeta> = {
  piping_admin: {
    id: "piping_admin",
    label: "Piping Admin",
    department: "Piping Administration",
    rank: 40,
  },
  pipe_lead: {
    id: "pipe_lead",
    label: "Pipe Lead",
    department: "Piping Lead Office",
    rank: 30,
  },
  pipe_stress_engineer: {
    id: "pipe_stress_engineer",
    label: "Pipe Stress Engineer",
    department: "Piping Engineering",
    rank: 20,
  },
  pipe_designer: {
    id: "pipe_designer",
    label: "Pipe Designer",
    department: "Piping Engineering",
    rank: 10,
  },
};

const ROLE_ALIASES: Record<string, string> = {
  user: "pipe_designer",
  developer: "pipe_stress_engineer",
  admin: "piping_admin",
  "pipe designer": "pipe_designer",
  "pipe stress engineer": "pipe_stress_engineer",
  "pipe stress enginner": "pipe_stress_engineer",
  "pipe lead": "pipe_lead",
  "piping admin": "piping_admin",
};

const DEFAULT_ROLE: OrgRoleMeta = ROLE_CATALOG.pipe_designer;

export function normalizeRoleId(role?: string): string {
  const key = (role || "").trim().toLowerCase();
  if (!key) return DEFAULT_ROLE.id;
  const mapped = ROLE_ALIASES[key] || key;
  return ROLE_CATALOG[mapped] ? mapped : DEFAULT_ROLE.id;
}

export function getRoleMeta(role?: string): OrgRoleMeta {
  return ROLE_CATALOG[normalizeRoleId(role)] || DEFAULT_ROLE;
}

export function getRoleLabel(role?: string): string {
  return getRoleMeta(role).label;
}

export function getRoleDepartment(role?: string): string {
  return getRoleMeta(role).department;
}

export const editableRoleCatalog = ROLE_CATALOG;
