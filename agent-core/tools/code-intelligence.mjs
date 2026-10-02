// Parse local JS/TS text using the project's installed compiler. Never evaluate it.
import ts from 'typescript';
import {readFileSync} from 'node:fs';
const files = JSON.parse(readFileSync(0, 'utf8'));
const output = {};
for (const {path, content} of files) {
  const file = ts.createSourceFile(path, content, ts.ScriptTarget.Latest, true);
  const result = {symbols: [], imports: [], diagnostics: [], parser: 'typescript-ast'};
  const line = node => file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1;
  const scopes = [];
  const nameOf = node => node?.name?.getText(file);
  function visit(node) {
    const named = (ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node) || ts.isInterfaceDeclaration(node)
      || ts.isMethodDeclaration(node) || ts.isConstructorDeclaration(node) || ts.isEnumDeclaration(node)
      || ts.isTypeAliasDeclaration(node) || (ts.isVariableDeclaration(node) && node.initializer
        && (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer))));
    const name = ts.isConstructorDeclaration(node) ? 'constructor' : nameOf(node);
    if (named && name) {
      const body = ts.isVariableDeclaration(node) ? node.initializer : node;
      const row = {path, name, qualified_name: [...scopes, name].join('.'),
        kind: ts.isClassDeclaration(node) ? 'class' : ts.isInterfaceDeclaration(node) ? 'interface'
          : ts.isTypeAliasDeclaration(node) ? 'type' : ts.isEnumDeclaration(node) ? 'enum' : 'function',
        line: line(node), end_line: file.getLineAndCharacterOfPosition(node.getEnd()).line + 1,
        signature: node.getText(file).split('\n')[0].slice(0, 240),
        parameters: body.parameters?.map(param => param.name.getText(file)) ?? [], calls: [], returns: []};
      function expressions(child) {
        if (child !== body && (ts.isFunctionDeclaration(child) || ts.isClassDeclaration(child)
          || ts.isArrowFunction(child) || ts.isFunctionExpression(child))) return;
        if (ts.isCallExpression(child) && row.calls.length < 16) {
          const target = child.expression.getText(file).slice(0, 120);
          if (!row.calls.includes(target)) row.calls.push(target);
        }
        if (ts.isReturnStatement(child) && child.expression && row.returns.length < 4)
          row.returns.push(child.expression.getText(file).slice(0, 240));
        ts.forEachChild(child, expressions);
      }
      if (body.body && row.kind === 'function') {
        expressions(body.body);
        if (ts.isArrowFunction(body) && !ts.isBlock(body.body)) row.returns.push(body.body.getText(file).slice(0, 240));
      }
      result.symbols.push(row);
      scopes.push(name);
      ts.forEachChild(node, visit);
      scopes.pop();
      return;
    }
    if (ts.isImportDeclaration(node) || ts.isImportEqualsDeclaration(node)
      || (ts.isCallExpression(node) && node.expression.getText(file) === 'require'))
      result.imports.push({path, line: line(node), text: node.getText(file).slice(0, 240)});
    ts.forEachChild(node, visit);
  }
  visit(file);
  result.diagnostics = file.parseDiagnostics.slice(0, 30).map(item => ({path,
    line: file.getLineAndCharacterOfPosition(item.start ?? 0).line + 1,
    severity: 'error', message: ts.flattenDiagnosticMessageText(item.messageText, ' ').slice(0, 500)}));
  output[path] = result;
}
process.stdout.write(JSON.stringify(output));
