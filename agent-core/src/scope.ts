import type {Artifact} from "./contracts.ts";

/**
 * Valida um caminho que será interpretado pelo runtime relativo ao workspace.
 * O runtime continua sendo a autoridade final, mas o adaptador não envia
 * intenções obviamente fora do escopo para ele.
 */
export function validateWorkspaceRelativePath(input: string): string {
  const path = String(input ?? "").trim().replaceAll("\\", "/");
  if (!path) throw new Error("O caminho do workspace não pode ser vazio.");
  if (path.includes("\0")) throw new Error("O caminho do workspace contém um byte nulo.");
  if (path.startsWith("/") || /^[A-Za-z]:\//.test(path)) {
    throw new Error("O caminho deve ser relativo ao workspace.");
  }

  const segments = path.split("/");
  if (segments.some((segment) => segment === "" || segment === "." || segment === "..")) {
    throw new Error("O caminho não pode sair do workspace.");
  }
  return segments.join("/");
}

export function validateWorkspaceArtifact(artifact: Artifact): Artifact {
  return {...artifact, path: validateWorkspaceRelativePath(artifact.path)};
}
