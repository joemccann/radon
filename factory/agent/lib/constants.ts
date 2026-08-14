/**
 * Reads a required environment variable, throwing if it is unset so
 * misconfiguration fails fast instead of surfacing mid-request.
 *
 * @remarks
 * Call it at module load when the value is needed for discovery (connector
 * UIDs, channel credentials), or inside a handler when a missing value
 * should not prevent the rest of the agent from loading.
 *
 * @param name - The environment variable name.
 * @param example - An example value, included in the error message.
 * @returns The environment variable's value.
 */
export function requireEnv(name: string, example: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `${name} environment variable is not set (e.g. '${example}').`
    );
  }
  return value;
}

/**
 * The repository the factory works on, as `owner/repo`.
 *
 * @remarks
 * Required at module load so a missing value fails discovery instead of
 * producing a factory with no target. Every surface reads this one constant:
 * the GitHub extension's default context, the station sandboxes' clone, and
 * the push URL.
 */
export const FACTORY_REPO = requireEnv("FACTORY_REPO", "acme/widgets");

const [factoryOwner, factoryRepoName, ...factoryRepoRest] =
  FACTORY_REPO.split("/");

if (!(factoryOwner && factoryRepoName) || factoryRepoRest.length > 0) {
  throw new Error(
    `FACTORY_REPO must be an "owner/repo" pair (e.g. 'acme/widgets'), got '${FACTORY_REPO}'.`
  );
}

/**
 * {@link FACTORY_REPO} split into the `owner` / `repo` fields GitHub tools
 * take, validated at module load.
 */
export const factoryRepo = { owner: factoryOwner, repo: factoryRepoName };

/**
 * The GitHub label that hands an issue to the factory. Overridable with the
 * `FACTORY_LABEL` environment variable.
 *
 * @remarks
 * Applying it requires triage permission on the repository, so the trigger is
 * maintainer-initiated even though the resulting run is unattended.
 */
export const FACTORY_LABEL = process.env.FACTORY_LABEL ?? "factory";

/**
 * Branch-name prefix for the factory's own feature branches. Overridable
 * with the `FACTORY_BRANCH_PREFIX` environment variable.
 *
 * @remarks
 * The implementer names its branches `factory/<type>-<slug>`; the GitHub
 * channel uses the prefix to recognize the factory's own pull requests, so
 * the red-CI fix loop never runs on branches people pushed. The implementer's
 * instructions carry the default prefix as prose, so an override should keep
 * them in sync (`agent/subagents/implementer/instructions.md`).
 */
export const FACTORY_BRANCH_PREFIX =
  process.env.FACTORY_BRANCH_PREFIX ?? "factory/";
