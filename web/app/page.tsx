import { WorkspaceMetadataMarker } from "./workspace-metadata-marker";
import ControlRoom from "./control-room";
import { workspaceMetadata } from "./workspace-location";

export function generateMetadata({ searchParams }: PageProps<"/">) {
  return workspaceMetadata("/", searchParams);
}

export default function Home() {
  return <>
    <ControlRoom />
    <WorkspaceMetadataMarker />
  </>;
}
