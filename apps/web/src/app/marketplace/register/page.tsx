import { SITES } from '@/lib/sites'
import { AuthLayout } from '@/components/auth/AuthLayout'
import { RegisterForm } from '@/components/auth/RegisterForm'

const site = SITES.marketplace!

export const metadata = {
  title: `Criar conta — ${site.displayName}`,
}

export default function MarketplaceRegisterPage() {
  return (
    <AuthLayout site={site}>
      <RegisterForm site={site} />
    </AuthLayout>
  )
}
