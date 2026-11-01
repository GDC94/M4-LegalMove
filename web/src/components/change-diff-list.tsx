import type { AmendmentKind, ChangeDetail } from "@/api/contract";
import { ChangeDiff } from "./change-diff";

/** One card per change, in the API's order; the same section can appear more than once. */
export function ChangeDiffList({
  changes,
  amendmentKind,
}: {
  changes: ChangeDetail[];
  amendmentKind: AmendmentKind | null;
}) {
  return (
    <ul className="space-y-3">
      {changes.map((detail, index) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: two changes of one section share id and type; the list never reorders
        <li key={index}>
          <ChangeDiff detail={detail} amendmentKind={amendmentKind} />
        </li>
      ))}
    </ul>
  );
}
