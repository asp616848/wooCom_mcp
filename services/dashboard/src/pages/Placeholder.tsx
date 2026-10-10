import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

/** Stand-in for pages built in later phases (servers P9, keys P9, audit P9, chat P10). */
export function Placeholder({ title, phase }: { title: string; phase: string }) {
  return (
    <Card className="max-w-xl">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>Coming in {phase}.</CardDescription>
      </CardHeader>
      <CardContent className="text-sm text-zinc-500">
        You are signed in; the gateway session works end to end.
      </CardContent>
    </Card>
  );
}
