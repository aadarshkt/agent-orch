import Navbar from '../components/Navbar'

export default function AppLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <>
      <Navbar />
      <div className="main-content">{children}</div>
    </>
  )
}
