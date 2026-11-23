import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "Smart Roster | Datastraw Operations",
  description: "Plan monthly workforce coverage with Datastraw Smart Roster.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
