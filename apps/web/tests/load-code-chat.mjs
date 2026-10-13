import { readFile } from 'node:fs/promises'
import ts from 'typescript'

const cache = new Map()
export async function moduleUrl(name) {
  if (cache.has(name)) return cache.get(name)
  const source = await readFile(new URL(`../src/${name}.ts`, import.meta.url), 'utf8')
  let { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  for (const [, specifier] of outputText.matchAll(/from ['"]([^'"]+)['"]/g)) {
    const url = specifier.startsWith('@/') ? await moduleUrl(specifier.slice(2)) : import.meta.resolve(specifier)
    outputText = outputText.replace(`from '${specifier}'`, `from '${url}'`).replace(`from "${specifier}"`, `from '${url}'`)
  }
  outputText = outputText.replaceAll('import.meta.env', '({ VITE_API_BASE_URL: "http://unit.invalid" })')
  const url = `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
  cache.set(name, url)
  return url
}
