import inspect
import sys
from copy import copy
from typing import Any, Callable, Dict, List, Tuple, Type, cast, get_type_hints

from typing_extensions import Annotated

from ._typing import get_args, get_origin, get_type_hints
from .models import ArgumentInfo, OptionInfo, ParameterInfo, ParamMeta


def _param_type_to_user_string(param_type: Type[ParameterInfo]) -> str:
    # Render a `ParameterInfo` subclass for use in error messages.
    # User code doesn't call `*Info` directly, so errors should present the classes how
    # they were (probably) defined in the user code.
    if param_type is OptionInfo:
        return "`Option`"
    elif param_type is ArgumentInfo:
        return "`Argument`"
    # This line shouldn't be reachable during normal use.
    return f"`{param_type.__name__}`"  # pragma: no cover


class AnnotatedParamWithDefaultValueError(Exception):
    argument_name: str
    param_type: Type[ParameterInfo]

    def __init__(self, argument_name: str, param_type: Type[ParameterInfo]):
        self.argument_name = argument_name
        self.param_type = param_type

    def __str__(self) -> str:
        param_type_str = _param_type_to_user_string(self.param_type)
        return (
            f"{param_type_str} default value cannot be set in `Annotated`"
            f" for {self.argument_name!r}. Set the default value with `=` instead."
        )


class MixedAnnotatedAndDefaultStyleError(Exception):
    argument_name: str
    annotated_param_type: Type[ParameterInfo]
    default_param_type: Type[ParameterInfo]

    def __init__(
        self,
        argument_name: str,
        annotated_param_type: Type[ParameterInfo],
        default_param_type: Type[ParameterInfo],
    ):
        self.argument_name = argument_name
        self.annotated_param_type = annotated_param_type
        self.default_param_type = default_param_type

    def __str__(self) -> str:
        annotated_param_type_str = _param_type_to_user_string(self.annotated_param_type)
        default_param_type_str = _param_type_to_user_string(self.default_param_type)
        msg = f"Cannot specify {annotated_param_type_str} in `Annotated` and"
        if self.annotated_param_type is self.default_param_type:
            msg += " default value"
        else:
            msg += f" {default_param_type_str} as a default value"
        msg += f" together for {self.argument_name!r}"
        return msg


class MultipleTyperAnnotationsError(Exception):
    argument_name: str

    def __init__(self, argument_name: str):
        self.argument_name = argument_name

    def __str__(self) -> str:
        return (
            "Cannot specify multiple `Annotated` Typer arguments"
            f" for {self.argument_name!r}"
        )


class DefaultFactoryAndDefaultValueError(Exception):
    argument_name: str
    param_type: Type[ParameterInfo]

    def __init__(self, argument_name: str, param_type: Type[ParameterInfo]):
        self.argument_name = argument_name
        self.param_type = param_type

    def __str__(self) -> str:
        param_type_str = _param_type_to_user_string(self.param_type)
        return (
            "Cannot specify `default_factory` and a default value together"
            f" for {param_type_str}"
        )


def _split_annotation_from_typer_annotations(
    base_annotation: Type[Any],
) -> Tuple[Type[Any], List[ParameterInfo]]:
    if get_origin(base_annotation) is not Annotated:  # type: ignore
        return base_annotation, []
    base_annotation, *maybe_typer_annotations = get_args(base_annotation)
    return base_annotation, [
        annotation
        for annotation in maybe_typer_annotations
        if isinstance(annotation, ParameterInfo)
    ]


def _expand_unpackable_param(p: inspect.Parameter) -> List[inspect.Parameter]:
    """Generates a list of inspect.Parameter from a TypedDict with annotations."""
    pa_args = get_args(p.annotation)
    pa0_type_hints = get_type_hints(pa_args[0], include_extras=True)
    print("")
    print("PARAMETER ANNOTATION STUFF:")
    print(f"p: {p}")
    print(f"p.annotation: {p.annotation}")
    print(f"pa_args: {pa_args}")
    print(f"pa0_type_hints: {pa0_type_hints}")
    print("")
    params = []
    for name, annotation in pa0_type_hints.items():
        annotation = copy(annotation)
        annotation_args = get_args(annotation)
        ba, [ta] = _split_annotation_from_typer_annotations(annotation)
        ta = copy(ta)
        print("")
        print("PARAMETER ARGS:")
        print(f"annotation_args: {annotation_args}")
        print(f"ba: {ba}")
        print(f"ta: {ta}")
        print(f"name: {name}")
        print(f"kind: {inspect.Parameter.KEYWORD_ONLY}")
        print(f"annotation: {annotation}")
        print("")

        # We only support optional options in unpackables. By default, assume default is None.
        def factory():
            return None

        # If there's a default_factory on the annotation, use that instead.
        # If we don't do this, the factory-produced value always takes precendent over the flag. Not sure why.
        if ta.default_factory is not None:
            factory = ta.default_factory
            ta.default_factory = None
        params.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=factory(),
                annotation=Annotated[ba, ta],
            )
        )

    return params

    # return [
    #     inspect.Parameter(
    #         name,
    #         inspect.Parameter.KEYWORD_ONLY,
    #         default=getattr(annotation, "default", inspect.Parameter.empty),
    #         annotation=annotation,
    #     )
    #     for name, annotation in get_type_hints(get_args(p.annotation)[0]).items()
    # ]


def _get_params_with_unpackables_expanded(
    sig: inspect.Signature,
) -> List[inspect.Parameter]:
    """If a parameter has a type of Unpack[TypedDict], expand it and add it to the list of func params."""
    all_params = []
    for _, param in sig.parameters.items():
        if get_origin(param.annotation) == Unpack:
            all_params.extend(_expand_unpackable_param(param))
        else:
            all_params.append(param)
    return all_params


def get_params_from_function(func: Callable[..., Any]) -> Dict[str, ParamMeta]:
    if sys.version_info >= (3, 10):
        signature = inspect.signature(func, eval_str=True)
    else:
        signature = inspect.signature(func)

    type_hints = get_type_hints(func)
    params = {}
    for param in _get_params_with_unpackables_expanded(signature):
        annotation, typer_annotations = _split_annotation_from_typer_annotations(
            param.annotation,
        )
        if len(typer_annotations) > 1:
            raise MultipleTyperAnnotationsError(param.name)

        print("")
        print("PARAMETER PARSED:")
        print(f"name: {param.name}")
        print(f"kind: {inspect.Parameter.KEYWORD_ONLY}")
        print(f"annotation: {param.annotation}")
        print(f"default: {param.default}")
        print(f"base_annotation: {annotation}")
        print(f"typer_annotation: {typer_annotations}")
        print(f"annotation_args: {get_args(param.annotation)}")
        print("")

        default = param.default
        if typer_annotations:
            # It's something like `my_param: Annotated[str, Argument()]`
            [parameter_info] = typer_annotations

            # Forbid `my_param: Annotated[str, Argument()] = Argument("...")`
            if isinstance(param.default, ParameterInfo):
                raise MixedAnnotatedAndDefaultStyleError(
                    argument_name=param.name,
                    annotated_param_type=type(parameter_info),
                    default_param_type=type(param.default),
                )

            parameter_info = copy(parameter_info)

            # When used as a default, `Option` takes a default value and option names
            # as positional arguments:
            #   `Option(some_value, "--some-argument", "-s")`
            # When used in `Annotated` (ie, what this is handling), `Option` just takes
            # option names as positional arguments:
            #   `Option("--some-argument", "-s")`
            # In this case, the `default` attribute of `parameter_info` is actually
            # meant to be the first item of `param_decls`.
            if (
                isinstance(parameter_info, OptionInfo)
                and parameter_info.default is not ...
            ):
                parameter_info.param_decls = (
                    cast(str, parameter_info.default),
                    *(parameter_info.param_decls or ()),
                )
                parameter_info.default = ...

            # Forbid `my_param: Annotated[str, Argument('some-default')]`
            if parameter_info.default is not ...:
                raise AnnotatedParamWithDefaultValueError(
                    param_type=type(parameter_info),
                    argument_name=param.name,
                )
            if param.default is not param.empty:
                # Put the parameter's default (set by `=`) into `parameter_info`, where
                # typer can find it.
                parameter_info.default = param.default

            default = parameter_info
        elif param.name in type_hints:
            # Resolve forward references.
            annotation = type_hints[param.name]

        if isinstance(default, ParameterInfo):
            parameter_info = copy(default)
            # Click supports `default` as either
            # - an actual value; or
            # - a factory function (returning a default value.)
            # The two are not interchangeable for static typing, so typer allows
            # specifying `default_factory`. Move the `default_factory` into `default`
            # so click can find it.
            if parameter_info.default is ... and parameter_info.default_factory:
                parameter_info.default = parameter_info.default_factory
            elif parameter_info.default_factory:
                raise DefaultFactoryAndDefaultValueError(
                    argument_name=param.name, param_type=type(parameter_info)
                )
            default = parameter_info

        params[param.name] = ParamMeta(
            name=param.name, default=default, annotation=annotation
        )
    return params
