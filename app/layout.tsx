import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "Datastraw Smart Roster",
  description: "A clear view of your team's shift coverage.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
