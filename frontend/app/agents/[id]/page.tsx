import ClientPage from "./ClientPage";

/** Static export: one placeholder param; client reads real id via useParams. */
export async function generateStaticParams() {
  return [{ id: "_" }];
}

export default function AgentIdPage() {
  return <ClientPage />;
}
