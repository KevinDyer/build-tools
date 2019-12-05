BitsModuleInfo = provider(fields = ["foo"])

def _bits_module_impl(ctx):
    out = ctx.actions.declare_file(ctx.label.name + ".tgz")

    args = ctx.actions.args()
    args.add_all("-m", [ctx.file.manifest.dirname])
    args.add_all("-o", [out.dirname + '/' + ctx.label.name])

    ctx.actions.run(
        arguments = [args],
        inputs = [ctx.file.manifest],
        outputs = [out],
        executable = ctx.executable._compiler,
    )
    return [DefaultInfo(files = depset([out]))]

bits_module = rule(
    implementation = _bits_module_impl,
    attrs = {
        "manifest": attr.label(
            allow_single_file = True,
            mandatory = True,
        ),
        "_compiler": attr.label(
            executable = True,
            cfg = "host",
            allow_files = True,
            default = Label("//:package-module"),
        ),
    },
)
