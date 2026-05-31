import puppeteer from 'puppeteer'

const BASE_URL = process.env.NEXTAUTH_URL ?? 'http://localhost:3000'

export async function GET(request: Request): Promise<Response> {
  const { searchParams } = new URL(request.url)
  const id = searchParams.get('id')

  if (!id) {
    return new Response(JSON.stringify({ error: 'id is required' }), {
      status: 400,
      headers: { 'content-type': 'application/json' },
    })
  }

  const browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  })

  try {
    const page = await browser.newPage()
    await page.setViewport({ width: 1280, height: 900 })
    await page.goto(`${BASE_URL}/analyze?id=${id}&print=true`, {
      waitUntil: 'networkidle2',
      timeout: 30_000,
    })
    const pdf = await page.pdf({ format: 'A4', printBackground: true })

    return new Response(pdf, {
      status: 200,
      headers: {
        'content-type': 'application/pdf',
        'content-disposition': `attachment; filename="supply-chain-${id}.pdf"`,
      },
    })
  } finally {
    await browser.close()
  }
}
